"""Tests for the shared tone-classification utilities.

Covers:
- classify_label — keyword-based label → tone mapping
- classify_score — numeric score → tone mapping
- engine_tone — combined label + score → tone
- Keyword sets are frozen and match expected membership
"""

import pytest
from app.utils.tone_classification import (
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
    NEUTRAL_KEYWORDS,
    classify_label,
    classify_score,
    engine_tone,
)


class TestClassifyLabel:
    """classify_label — label string → bullish/bearish/neutral/unknown."""

    @pytest.mark.parametrize("label,expected", [
        ("Bullish", "bullish"),
        ("bullish", "bullish"),
        ("BULLISH", "bullish"),
        ("Favored", "bullish"),
        ("Strongly Favored", "bullish"),
        ("Strongly_Favored", "bullish"),
        ("strongly_favored", "bullish"),
        ("Supportive", "bullish"),
        ("Positive", "bullish"),
        ("Strong", "bullish"),
        ("Expansion", "bullish"),
        ("Broadening", "bullish"),
        # Compound with bullish token
        ("Premium Selling Strongly Favored", "bullish"),
    ])
    def test_bullish(self, label, expected):
        assert classify_label(label) == expected

    @pytest.mark.parametrize("label,expected", [
        ("Bearish", "bearish"),
        ("Cautious", "bearish"),
        ("Cautionary", "bearish"),
        ("Unfavorable", "bearish"),
        ("Negative", "bearish"),
        ("Weak", "bearish"),
        ("Contraction", "bearish"),
        ("Narrowing", "bearish"),
        ("Risk Off", "bearish"),
        ("Risk_Off", "bearish"),
        ("risk_off", "bearish"),
        ("Stress", "bearish"),
        ("Elevated Risk", "bearish"),
        ("Elevated_Risk", "bearish"),
    ])
    def test_bearish(self, label, expected):
        assert classify_label(label) == expected

    @pytest.mark.parametrize("label,expected", [
        ("Neutral", "neutral"),
        ("Mixed", "neutral"),
        ("Moderate", "neutral"),
        ("Unclear", "neutral"),
    ])
    def test_neutral(self, label, expected):
        assert classify_label(label) == expected

    @pytest.mark.parametrize("label,expected", [
        (None, "unknown"),
        ("", "unknown"),
        ("SomeRandomThing", "unknown"),
        ("CustomLabel", "unknown"),
    ])
    def test_unknown(self, label, expected):
        assert classify_label(label) == expected


class TestClassifyScore:
    """classify_score — numeric score → bullish/bearish/neutral/unknown."""

    @pytest.mark.parametrize("score,expected", [
        (65.0, "bullish"),
        (80.0, "bullish"),
        (100.0, "bullish"),
        (35.0, "bearish"),
        (20.0, "bearish"),
        (0.0, "bearish"),
        (50.0, "neutral"),
        (36.0, "neutral"),
        (64.0, "neutral"),
        (None, "unknown"),
    ])
    def test_thresholds(self, score, expected):
        assert classify_score(score) == expected


class TestEngineTone:
    """engine_tone — combined label + score derivation."""

    def test_label_priority_over_score(self):
        """Label bullish should override a neutral score."""
        norm = {"short_label": "Bullish", "score": 50.0}
        assert engine_tone(norm) == "bullish"

    def test_score_fallback_when_label_unknown(self):
        """Unknown label → score determines tone."""
        norm = {"short_label": "CustomRegime", "score": 80.0}
        assert engine_tone(norm) == "bullish"

    def test_both_neutral(self):
        norm = {"short_label": "Mixed", "score": 50.0}
        assert engine_tone(norm) == "neutral"

    def test_both_unknown(self):
        """Both label and score unknown → unknown."""
        norm = {"short_label": None, "score": None}
        assert engine_tone(norm) == "unknown"

    def test_label_bearish_overrides_bullish_score(self):
        norm = {"short_label": "Bearish", "score": 80.0}
        assert engine_tone(norm) == "bearish"

    def test_label_field_fallback(self):
        """Uses 'label' when 'short_label' is empty."""
        norm = {"short_label": "", "label": "Bullish", "score": 50.0}
        assert engine_tone(norm) == "bullish"

    def test_neutral_label_with_bullish_score(self):
        """Neutral label + bullish score → bullish (score breaks tie)."""
        norm = {"short_label": "Neutral", "score": 80.0}
        assert engine_tone(norm) == "bullish"


class TestKeywordSets:
    """Ensure keyword set membership is stable and frozen."""

    def test_sets_are_frozenset(self):
        assert isinstance(BULLISH_KEYWORDS, frozenset)
        assert isinstance(BEARISH_KEYWORDS, frozenset)
        assert isinstance(NEUTRAL_KEYWORDS, frozenset)

    def test_no_overlap(self):
        """No word appears in multiple keyword sets."""
        assert not (BULLISH_KEYWORDS & BEARISH_KEYWORDS)
        assert not (BULLISH_KEYWORDS & NEUTRAL_KEYWORDS)
        assert not (BEARISH_KEYWORDS & NEUTRAL_KEYWORDS)

    def test_expected_bullish_members(self):
        for kw in ("bullish", "favored", "strongly_favored", "supportive",
                    "positive", "strong", "expansion", "broadening"):
            assert kw in BULLISH_KEYWORDS

    def test_expected_bearish_members(self):
        for kw in ("bearish", "cautious", "cautionary", "unfavorable",
                    "negative", "weak", "contraction", "narrowing",
                    "risk_off", "stress", "elevated_risk"):
            assert kw in BEARISH_KEYWORDS

    def test_expected_neutral_members(self):
        for kw in ("neutral", "mixed", "moderate", "unclear"):
            assert kw in NEUTRAL_KEYWORDS
