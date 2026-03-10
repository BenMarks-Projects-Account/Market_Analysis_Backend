"""Tests for the engine explanation builder.

Covers the five required scenarios plus helper function unit tests.
"""

from __future__ import annotations

import pytest

from app.services.news_sentiment_engine import (
    build_engine_explanation,
    _interpret_component,
    _contribution_label,
    _WEIGHTS,
    _DISPLAY_NAMES,
    _TOOLTIPS,
)


# ── Helpers for building component dicts ────────────────────────────

def _make_components(**overrides: float) -> dict:
    """Build a components dict with default score 50 for each, overridden by kwargs."""
    defaults = {
        "headline_sentiment": 50.0,
        "negative_pressure": 50.0,
        "narrative_severity": 50.0,
        "source_agreement": 50.0,
        "macro_stress": 50.0,
        "recency_pressure": 50.0,
    }
    defaults.update(overrides)
    return {name: {"score": score, "signals": [], "inputs": {}} for name, score in defaults.items()}


# ── Scenario 1: High negative pressure + high macro stress ─────────

class TestHighNegativePressureHighMacroStress:
    """When negative_pressure is LOW (heavy bearish) and macro_stress is LOW (severe stress)."""

    @pytest.fixture
    def result(self):
        comps = _make_components(
            headline_sentiment=45.0,
            negative_pressure=20.0,
            narrative_severity=30.0,
            source_agreement=55.0,
            macro_stress=15.0,
            recency_pressure=35.0,
        )
        composite = 30.0
        return build_engine_explanation(composite, "Mixed", comps, _WEIGHTS)

    def test_label_is_mixed(self, result):
        assert result["label"] == "MIXED"

    def test_composite_score(self, result):
        assert result["composite_score"] == 30.0

    def test_negative_contributors_exist(self, result):
        negs = result["score_logic"]["largest_negative_contributors"]
        assert len(negs) >= 2
        # Both negative_pressure and macro_stress should appear as negative
        names = " ".join(negs)
        assert "Negative Pressure" in names or "Macro Stress" in names

    def test_trader_takeaway_defensive(self, result):
        assert "headwinds" in result["trader_takeaway"].lower() or "stress" in result["trader_takeaway"].lower()

    def test_component_analysis_has_all_six(self, result):
        assert len(result["component_analysis"]) == 6

    def test_interpretations_not_empty(self, result):
        for ca in result["component_analysis"]:
            assert ca["interpretation"], f"Missing interpretation for {ca['component']}"


# ── Scenario 2: High source agreement but mixed narrative ──────────

class TestHighAgreementMixedNarrative:
    """Sources agree (high) but narrative severity is middling."""

    @pytest.fixture
    def result(self):
        comps = _make_components(
            headline_sentiment=55.0,
            negative_pressure=60.0,
            narrative_severity=45.0,  # mid-range, mixed narrative
            source_agreement=85.0,   # strong agreement
            macro_stress=60.0,
            recency_pressure=50.0,
        )
        composite = 58.0
        return build_engine_explanation(composite, "Neutral", comps, _WEIGHTS)

    def test_source_agreement_is_positive(self, result):
        pos = result["score_logic"]["largest_positive_contributors"]
        names = " ".join(pos)
        assert "Source Agreement" in names

    def test_narrative_severity_is_balancing(self, result):
        bal = result["score_logic"]["balancing_forces"]
        names = " ".join(bal)
        assert "Narrative" in names

    def test_summary_mentions_neutral(self, result):
        assert "neutral" in result["summary"].lower()


# ── Scenario 3: Fresh but conflicting headlines ────────────────────

class TestFreshConflictingHeadlines:
    """Recency is strongly negative but headline sentiment is high — conflict."""

    @pytest.fixture
    def result(self):
        comps = _make_components(
            headline_sentiment=72.0,
            negative_pressure=65.0,
            narrative_severity=60.0,
            source_agreement=30.0,  # low agreement = conflicting
            macro_stress=55.0,
            recency_pressure=22.0,  # very bearish recent
        )
        composite = 52.0
        return build_engine_explanation(composite, "Neutral", comps, _WEIGHTS)

    def test_recency_is_negative(self, result):
        negs = result["score_logic"]["largest_negative_contributors"]
        names = " ".join(negs)
        assert "Signal Freshness" in names or "Recency" in names

    def test_source_agreement_negative(self, result):
        negs = result["score_logic"]["largest_negative_contributors"]
        names = " ".join(negs)
        assert "Source Agreement" in names

    def test_headline_sentiment_is_positive(self, result):
        pos = result["score_logic"]["largest_positive_contributors"]
        names = " ".join(pos)
        assert "Headline Strength" in names

    def test_signal_quality_explanation_exists(self, result):
        assert result["signal_quality"]["explanation"]


# ── Scenario 4: Strongly constructive ──────────────────────────────

class TestStronglyConstructive:
    """Nearly all components bullish → Risk-On."""

    @pytest.fixture
    def result(self):
        comps = _make_components(
            headline_sentiment=82.0,
            negative_pressure=90.0,
            narrative_severity=78.0,
            source_agreement=75.0,
            macro_stress=85.0,
            recency_pressure=70.0,
        )
        composite = 82.0
        return build_engine_explanation(composite, "Risk-On", comps, _WEIGHTS)

    def test_label_bullish(self, result):
        assert result["label"] == "BULLISH"

    def test_all_positive_contributors(self, result):
        pos = result["score_logic"]["largest_positive_contributors"]
        assert len(pos) == 6

    def test_no_negative_contributors(self, result):
        negs = result["score_logic"]["largest_negative_contributors"]
        assert len(negs) == 0

    def test_signal_quality_high(self, result):
        assert result["signal_quality"]["strength"] == "high"

    def test_trader_takeaway_constructive(self, result):
        assert "constructive" in result["trader_takeaway"].lower()

    def test_summary_constructive(self, result):
        assert "constructive" in result["summary"].lower()


# ── Scenario 5: Neutral/mixed balancing ────────────────────────────

class TestNeutralBalancing:
    """All components near 50 → everything is balancing, low signal."""

    @pytest.fixture
    def result(self):
        comps = _make_components(
            headline_sentiment=48.0,
            negative_pressure=52.0,
            narrative_severity=47.0,
            source_agreement=50.0,
            macro_stress=51.0,
            recency_pressure=49.0,
        )
        composite = 49.5
        return build_engine_explanation(composite, "Neutral", comps, _WEIGHTS)

    def test_label_neutral(self, result):
        assert result["label"] == "NEUTRAL"

    def test_all_balancing(self, result):
        bal = result["score_logic"]["balancing_forces"]
        assert len(bal) == 6

    def test_no_positive_or_negative(self, result):
        assert len(result["score_logic"]["largest_positive_contributors"]) == 0
        assert len(result["score_logic"]["largest_negative_contributors"]) == 0

    def test_signal_quality_low(self, result):
        assert result["signal_quality"]["strength"] == "low"

    def test_summary_mentions_neutral(self, result):
        assert "neutral" in result["summary"].lower()

    def test_trader_takeaway_neutral(self, result):
        assert "neutral" in result["trader_takeaway"].lower() or "condor" in result["trader_takeaway"].lower()


# ── Unit tests for helper functions ────────────────────────────────

class TestInterpretComponent:
    """Test _interpret_component for each component across score ranges."""

    @pytest.mark.parametrize("score,fragment", [
        (80.0, "constructive"),
        (50.0, "balanced"),
        (20.0, "bearish"),
    ])
    def test_headline_sentiment(self, score, fragment):
        result = _interpret_component("headline_sentiment", score)
        assert fragment in result.lower()

    @pytest.mark.parametrize("score,fragment", [
        (90.0, "few bearish"),
        (50.0, "meaningful bearish"),
        (15.0, "heavy bearish"),
    ])
    def test_negative_pressure(self, score, fragment):
        result = _interpret_component("negative_pressure", score)
        assert fragment in result.lower()

    @pytest.mark.parametrize("score,fragment", [
        (85.0, "calm"),
        (50.0, "moderate"),
        (10.0, "severe"),
    ])
    def test_macro_stress(self, score, fragment):
        result = _interpret_component("macro_stress", score)
        assert fragment in result.lower()


class TestContributionLabel:
    def test_positive(self):
        assert _contribution_label(75.0) == "positive"

    def test_negative(self):
        assert _contribution_label(25.0) == "negative"

    def test_neutral(self):
        assert _contribution_label(50.0) == "neutral"

    def test_boundary_60(self):
        assert _contribution_label(60.0) == "positive"

    def test_boundary_40(self):
        assert _contribution_label(40.0) == "negative"


class TestExplanationStructure:
    """Verify the returned dict has all required keys and correct types."""

    @pytest.fixture
    def result(self):
        comps = _make_components()
        return build_engine_explanation(50.0, "Neutral", comps, _WEIGHTS)

    def test_top_level_keys(self, result):
        required = {"label", "composite_score", "summary", "component_analysis",
                     "score_logic", "signal_quality", "trader_takeaway"}
        assert required.issubset(set(result.keys()))

    def test_component_analysis_fields(self, result):
        for ca in result["component_analysis"]:
            assert "component" in ca
            assert "display_name" in ca
            assert "score" in ca
            assert "weight" in ca
            assert "interpretation" in ca
            assert "contribution" in ca
            assert "tooltip" in ca

    def test_score_logic_keys(self, result):
        sl = result["score_logic"]
        assert "largest_positive_contributors" in sl
        assert "largest_negative_contributors" in sl
        assert "balancing_forces" in sl

    def test_signal_quality_keys(self, result):
        sq = result["signal_quality"]
        assert "strength" in sq
        assert "explanation" in sq

    def test_display_names_mapping(self, result):
        for ca in result["component_analysis"]:
            assert ca["display_name"] == _DISPLAY_NAMES[ca["component"]]

    def test_tooltips_present(self, result):
        for ca in result["component_analysis"]:
            assert ca["tooltip"] == _TOOLTIPS[ca["component"]]


class TestLabelMapping:
    """Verify regime_label → explanation label mapping."""

    @pytest.mark.parametrize("regime,expected", [
        ("Risk-On", "BULLISH"),
        ("Neutral", "NEUTRAL"),
        ("Mixed", "MIXED"),
        ("Risk-Off", "RISK-OFF"),
        ("High Stress", "RISK-OFF"),
    ])
    def test_label_mapping(self, regime, expected):
        comps = _make_components()
        result = build_engine_explanation(50.0, regime, comps, _WEIGHTS)
        assert result["label"] == expected
