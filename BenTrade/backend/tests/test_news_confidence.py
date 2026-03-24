"""Tests for news sentiment engine _compute_confidence() and its integration."""

import pytest
from datetime import datetime, timezone, timedelta

from app.services.news_sentiment_engine import (
    _compute_confidence,
    compute_engine_scores,
)


# ═══════════════════════════════════════════════════════════════════════
# UNIT TESTS: _compute_confidence()
# ═══════════════════════════════════════════════════════════════════════


class TestComputeConfidenceUnit:
    """Direct unit tests for _compute_confidence()."""

    def test_zero_headlines_low_confidence(self):
        """0 headlines → confidence < 30 (no_headlines + all_defaulted + proxy)."""
        conf, penalties = _compute_confidence(
            headline_count=0,
            source_count=0,
            defaulted_component_count=6,
            total_components=6,
            macro_context_available=False,
        )
        assert conf < 30, f"Expected < 30 with zero data, got {conf}"
        assert any("no_headlines" in p for p in penalties)
        assert any("all_components_defaulted" in p for p in penalties)

    def test_rich_data_high_confidence(self):
        """20 headlines from 5 sources, 0 defaulted, macro available → > 70."""
        conf, penalties = _compute_confidence(
            headline_count=20,
            source_count=5,
            defaulted_component_count=0,
            total_components=6,
            macro_context_available=True,
        )
        assert conf > 70, f"Expected > 70 with rich data, got {conf}"
        # Only the permanent keyword proxy penalty should apply
        assert len(penalties) == 1
        assert "keyword_sentiment_proxy" in penalties[0]

    def test_moderate_data_middling_confidence(self):
        """5 headlines from 1 source → ~50-60 range."""
        conf, penalties = _compute_confidence(
            headline_count=5,
            source_count=1,
            defaulted_component_count=1,
            total_components=6,
            macro_context_available=True,
        )
        assert 40 <= conf <= 80, f"Expected moderate confidence, got {conf}"
        assert any("single_source" in p for p in penalties)

    def test_keyword_proxy_always_penalized(self):
        """The keyword sentiment proxy penalty is always applied."""
        conf, penalties = _compute_confidence(
            headline_count=50,
            source_count=10,
            defaulted_component_count=0,
            total_components=6,
            macro_context_available=True,
        )
        assert any("keyword_sentiment_proxy" in p for p in penalties)

    def test_no_macro_context_penalty(self):
        """Missing macro context should add a penalty."""
        conf_with, _ = _compute_confidence(
            headline_count=20,
            source_count=5,
            defaulted_component_count=0,
            total_components=6,
            macro_context_available=True,
        )
        conf_without, pen_without = _compute_confidence(
            headline_count=20,
            source_count=5,
            defaulted_component_count=0,
            total_components=6,
            macro_context_available=False,
        )
        assert conf_without < conf_with
        assert any("no_macro_context" in p for p in pen_without)

    def test_confidence_clamped_0_100(self):
        """Confidence must stay in [0, 100] even with extreme penalties."""
        conf, _ = _compute_confidence(
            headline_count=0,
            source_count=0,
            defaulted_component_count=6,
            total_components=6,
            macro_context_available=False,
        )
        assert 0.0 <= conf <= 100.0

    def test_very_few_headlines_penalty(self):
        """2 headlines → very_few_headlines penalty."""
        _, penalties = _compute_confidence(
            headline_count=2,
            source_count=2,
            defaulted_component_count=0,
            total_components=6,
            macro_context_available=True,
        )
        assert any("very_few_headlines" in p for p in penalties)

    def test_most_components_defaulted(self):
        """4 of 6 defaulted → most_components_defaulted penalty."""
        _, penalties = _compute_confidence(
            headline_count=10,
            source_count=3,
            defaulted_component_count=4,
            total_components=6,
            macro_context_available=True,
        )
        assert any("most_components_defaulted" in p for p in penalties)

    def test_some_components_defaulted(self):
        """2 of 6 defaulted → some_components_defaulted penalty."""
        _, penalties = _compute_confidence(
            headline_count=10,
            source_count=3,
            defaulted_component_count=2,
            total_components=6,
            macro_context_available=True,
        )
        assert any("some_components_defaulted" in p for p in penalties)


# ═══════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS: confidence in compute_engine_scores() output
# ═══════════════════════════════════════════════════════════════════════


def _make_headline(source="finnhub", sentiment_score=0.1, sentiment_label="bullish",
                   category="general", hours_ago=2):
    """Helper to create a realistic headline dict."""
    pub = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {
        "headline": "Markets gain on strong earnings",
        "summary": "Stocks soar as earnings beat forecasts",
        "source": source,
        "published_at": pub,
        "category": category,
        "sentiment_score": sentiment_score,
        "sentiment_label": sentiment_label,
    }


class TestConfidenceIntegration:
    """Verify confidence is properly wired into engine output."""

    def test_empty_items_low_confidence(self):
        """Empty items list → confidence should be very low."""
        result = compute_engine_scores([], {})
        assert "confidence" in result
        assert "confidence_score" in result
        assert "confidence_penalties" in result
        # All components default to 50 with no items, confidence should be low
        assert result["confidence"] < 40

    def test_many_headlines_high_confidence(self):
        """20 headlines from 5 sources with macro → high confidence."""
        items = []
        sources = ["finnhub", "polygon", "reuters", "bloomberg", "cnbc"]
        for i in range(20):
            items.append(_make_headline(
                source=sources[i % len(sources)],
                sentiment_score=0.2,
                hours_ago=i,
            ))
        macro = {"stress_level": "low", "vix": 14.0, "yield_curve_spread": 0.5}
        result = compute_engine_scores(items, macro)
        assert result["confidence"] > 70, f"Expected > 70, got {result['confidence']}"

    def test_single_source_moderate_confidence(self):
        """5 headlines from 1 source → moderate confidence."""
        items = [_make_headline(source="finnhub", hours_ago=i) for i in range(5)]
        macro = {"stress_level": "moderate", "vix": 20.0}
        result = compute_engine_scores(items, macro)
        assert 40 <= result["confidence"] <= 80
        assert any("single_source" in p for p in result["confidence_penalties"])

    def test_confidence_keys_match(self):
        """Both confidence and confidence_score should exist and match."""
        result = compute_engine_scores([], {})
        assert result["confidence"] == result["confidence_score"]

    def test_regime_service_can_extract_confidence(self):
        """Simulate regime_service extraction: key lookup and normalization."""
        result = compute_engine_scores([], {})
        # Regime service tries "confidence" first, then "confidence_score"
        conf = result.get("confidence")
        if conf is None:
            conf = result.get("confidence_score")
        assert conf is not None
        # Normalize 0-100 → 0-1 (mirroring regime_service logic)
        if conf > 1.0:
            conf = conf / 100.0
        assert 0.0 <= conf <= 1.0
