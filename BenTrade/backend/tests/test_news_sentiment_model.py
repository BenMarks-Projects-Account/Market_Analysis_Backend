"""Tests for enhanced news sentiment model analysis pipeline.

Covers:
  - Bullish market news cluster
  - Bearish macro shock cluster
  - Mixed geopolitical + sector resilience cluster
  - Malformed JSON with successful repair
  - Malformed JSON with graceful fallback
  - <think> tag stripping
  - Schema coercion from new and legacy shapes
"""
import json

from common.json_repair import extract_and_repair_json
from common.model_analysis import (
    _coerce_news_sentiment_model_output,
    _strip_think_tags,
)


# ─── Fixtures ────────────────────────────────────────────────────────────

BULLISH_FIXTURE = {
    "label": "BULLISH",
    "score": 78,
    "confidence": 0.82,
    "tone": "Bullish",
    "summary": (
        "Markets rallied broadly as strong earnings from mega-cap tech and a dovish Fed "
        "tone reinforced risk appetite. VIX dropped below 14 suggesting complacency. "
        "Traders should favor premium-selling strategies with a bullish tilt, though "
        "low vol itself is a risk."
    ),
    "headline_drivers": [
        {
            "theme": "Tech Earnings Beat",
            "impact": "bullish",
            "strength": 4,
            "explanation": "AAPL, MSFT, and GOOGL all beat revenue estimates, lifting Nasdaq 2%.",
        },
        {
            "theme": "Fed Dovish Pivot",
            "impact": "bullish",
            "strength": 3,
            "explanation": "Powell signaled rate cuts likely in Q3, easing financial conditions.",
        },
    ],
    "major_headlines": [
        {
            "headline": "S&P 500 hits record high as tech earnings surge",
            "category": "earnings",
            "market_impact": "bullish",
            "why_it_matters": "Broad-based rally confirms risk-on, not just narrow tech leadership.",
        },
    ],
    "score_drivers": {
        "bullish_factors": [
            "Strong mega-cap earnings across tech sector",
            "Dovish Fed tone supporting rate-cut expectations",
            "VIX below 14 indicating low fear",
        ],
        "bearish_factors": [
            "VIX complacency could set up a vol spike",
        ],
        "offsetting_factors": [
            "Low volume suggests conviction may be thin",
        ],
    },
    "market_implications": {
        "equities": "Constructive, favor long delta with premium selling overlays",
        "volatility": "Low vol is tradeable but fragile — watch for vol-of-vol spikes",
        "rates": "Curve steepening expected if cuts materialize in Q3",
        "energy_or_commodities": "Oil steady, not a headwind",
        "sector_rotation": "Growth over value, tech leadership continues",
    },
    "uncertainty_flags": [
        "Low VIX can reverse quickly on any geopolitical shock",
    ],
    "trader_takeaway": (
        "The setup favors selling premium on index ETFs with a bullish tilt. Iron condors "
        "and bull put spreads on SPY/QQQ offer good risk-reward at current vol levels. "
        "Remain nimble — VIX at 14 means gamma risk is asymmetric to the downside."
    ),
}

BEARISH_FIXTURE = {
    "label": "RISK-OFF",
    "score": 18,
    "confidence": 0.88,
    "tone": "Bearish",
    "summary": (
        "Markets sold off sharply as hot CPI data killed rate-cut hopes and a surprise "
        "geopolitical flare-up in the Middle East rattled energy markets. Oil surged 6% "
        "and the VIX spiked above 28. Traders should reduce risk, widen spreads, and "
        "consider defensive positioning."
    ),
    "headline_drivers": [
        {
            "theme": "CPI Inflation Shock",
            "impact": "bearish",
            "strength": 5,
            "explanation": "Core CPI came in at 4.2% vs 3.8% expected, shattering rate-cut hopes.",
        },
        {
            "theme": "Middle East Escalation",
            "impact": "bearish",
            "strength": 4,
            "explanation": "Iran-Israel tensions sent oil above $95, raising stagflation fears.",
        },
    ],
    "major_headlines": [
        {
            "headline": "CPI surges past expectations, markets tank",
            "category": "macro",
            "market_impact": "bearish",
            "why_it_matters": "Removes any near-term possibility of rate cuts, tightening financial conditions.",
        },
    ],
    "score_drivers": {
        "bullish_factors": [],
        "bearish_factors": [
            "CPI 4.2% vs 3.8% expected",
            "Oil surge above $95 creating stagflation risk",
            "VIX at 28 — elevated fear",
            "Fed rate cuts completely repriced out",
        ],
        "offsetting_factors": [],
    },
    "market_implications": {
        "equities": "Defensive — expect continued selling pressure",
        "volatility": "Elevated and likely to stay elevated this week",
        "rates": "10Y yield breaking above 4.8%, mortgage rates following",
        "energy_or_commodities": "Oil supply disruption risk is persistent",
        "sector_rotation": "Rotation to staples, utilities, and cash",
    },
    "uncertainty_flags": [
        "Geopolitical situation is fluid — could escalate or de-escalate rapidly",
        "CPI data may have seasonal distortions",
    ],
    "trader_takeaway": (
        "This is not the environment for aggressive selling premium. Widen your spreads, "
        "reduce position sizes, and consider bear call spreads on tech. If you're holding "
        "short vol, hedge with VIX calls or reduce exposure immediately."
    ),
}

MIXED_FIXTURE = {
    "label": "MIXED",
    "score": 48,
    "confidence": 0.55,
    "tone": "Mixed",
    "summary": (
        "Cross-currents dominate: strong domestic earnings offset by escalating trade "
        "tensions with China. Tech resilience argues against broad de-risking, but tariff "
        "uncertainty caps upside. VIX hovering at 19 signals markets are pricing in some "
        "risk but not panic. Traders should neutral-hedge and wait for clarity."
    ),
    "headline_drivers": [
        {
            "theme": "US-China Trade Tensions",
            "impact": "bearish",
            "strength": 4,
            "explanation": "New tariff threats on semiconductors rattled supply-chain stocks.",
        },
        {
            "theme": "Tech Sector Resilience",
            "impact": "bullish",
            "strength": 3,
            "explanation": "Despite macro headwinds, cloud and AI spend continues to accelerate.",
        },
    ],
    "major_headlines": [
        {
            "headline": "US threatens new tariffs on Chinese semiconductors",
            "category": "geopolitics",
            "market_impact": "bearish",
            "why_it_matters": "Could disrupt chip supply chains and raise costs for US tech firms.",
        },
        {
            "headline": "Cloud revenue growth accelerates for major tech firms",
            "category": "earnings",
            "market_impact": "bullish",
            "why_it_matters": "Shows AI-driven demand is real, supporting tech valuations.",
        },
    ],
    "score_drivers": {
        "bullish_factors": [
            "Tech earnings continue to beat expectations",
            "AI capital spend accelerating",
        ],
        "bearish_factors": [
            "New tariff threats on semiconductors",
            "Trade policy uncertainty rising",
        ],
        "offsetting_factors": [
            "Tariff rhetoric may be negotiating tactic, not policy",
            "Tech outperformance may be narrow and fragile",
        ],
    },
    "market_implications": {
        "equities": "Range-bound until trade resolution — avoid directional bets",
        "volatility": "Moderate and likely to stay choppy",
        "rates": "Stable — Fed on hold watching trade developments",
        "energy_or_commodities": "Neutral, no major commodity catalyst",
        "sector_rotation": "Quality over beta — favor profitable tech over speculative growth",
    },
    "uncertainty_flags": [
        "Trade outcome is binary — negotiation vs escalation are both possible",
        "Market positioning is light, so moves could be amplified",
    ],
    "trader_takeaway": (
        "This is an iron-condor environment. Elevated vol with no clear direction means "
        "premium is rich enough to sell, but keep positions small and well-hedged. SPY "
        "4-wide iron condors centered around current price offer balanced risk-reward."
    ),
}


# ─── Coercion Tests ──────────────────────────────────────────────────────

def test_bullish_coercion():
    result = _coerce_news_sentiment_model_output(BULLISH_FIXTURE)
    assert result is not None
    assert result["label"] == "BULLISH"
    assert result["score"] == 78
    assert result["confidence"] == 0.82
    assert result["tone"] == "Bullish"
    assert "rallied broadly" in result["summary"]
    assert len(result["headline_drivers"]) == 2
    assert result["headline_drivers"][0]["theme"] == "Tech Earnings Beat"
    assert result["headline_drivers"][0]["impact"] == "bullish"
    assert result["headline_drivers"][0]["strength"] == 4
    assert len(result["score_drivers"]["bullish_factors"]) == 3
    assert len(result["score_drivers"]["bearish_factors"]) == 1
    assert result["market_implications"]["equities"] is not None
    assert len(result["uncertainty_flags"]) == 1
    assert "iron condors" in result["trader_takeaway"].lower() or "premium" in result["trader_takeaway"].lower()


def test_bearish_coercion():
    result = _coerce_news_sentiment_model_output(BEARISH_FIXTURE)
    assert result is not None
    assert result["label"] == "RISK-OFF"
    assert result["score"] == 18
    assert result["confidence"] == 0.88
    assert len(result["score_drivers"]["bearish_factors"]) == 4
    assert result["score_drivers"]["bullish_factors"] is None  # empty list → None
    assert result["trader_takeaway"] is not None


def test_mixed_coercion():
    result = _coerce_news_sentiment_model_output(MIXED_FIXTURE)
    assert result is not None
    assert result["label"] == "MIXED"
    assert result["score"] == 48
    assert result["confidence"] == 0.55
    assert len(result["score_drivers"]["offsetting_factors"]) == 2
    assert len(result["uncertainty_flags"]) == 2
    assert len(result["major_headlines"]) == 2


def test_legacy_schema_backward_compat():
    """Ensure the old schema shape (regime_label, executive_summary, headline_tone) still works."""
    legacy = {
        "regime_label": "Risk-On",
        "score": 72,
        "confidence": 0.75,
        "headline_tone": "Bullish",
        "executive_summary": "Markets look constructive.",
        "dominant_narratives": ["tech rally", "dovish fed"],
        "key_drivers": ["earnings", "rates"],
        "underpriced_risks": ["geopolitics"],
        "change_triggers": ["CPI surprise"],
    }
    result = _coerce_news_sentiment_model_output(legacy)
    assert result is not None
    # Legacy regime_label maps to label (uppercase)
    assert result["label"] == "RISK-ON"
    assert result["score"] == 72
    assert result["tone"] == "Bullish"
    # Legacy executive_summary maps to summary
    assert result["summary"] == "Markets look constructive."
    # Legacy lists preserved
    assert result["dominant_narratives"] == ["tech rally", "dovish fed"]
    assert result["key_drivers"] == ["earnings", "rates"]


def test_score_bounds():
    out = _coerce_news_sentiment_model_output({"label": "BULLISH", "score": 150, "confidence": 2.5})
    assert out["score"] == 100.0
    assert out["confidence"] == 1.0

    out2 = _coerce_news_sentiment_model_output({"label": "BEARISH", "score": -10, "confidence": -0.5})
    assert out2["score"] == 0.0
    assert out2["confidence"] == 0.0


def test_invalid_label_defaults():
    out = _coerce_news_sentiment_model_output({"label": "SUPER_BULLISH", "score": 80})
    assert out["label"] == "NEUTRAL"


def test_none_input():
    assert _coerce_news_sentiment_model_output(None) is None
    assert _coerce_news_sentiment_model_output("not a dict") is None
    assert _coerce_news_sentiment_model_output(42) is None


# ─── JSON Repair Tests ──────────────────────────────────────────────────

def test_malformed_json_repair_success():
    """JSON with trailing commas + Python literals should repair successfully."""
    raw = json.dumps(BULLISH_FIXTURE)
    # Inject trailing comma and Python literal
    mangled = raw.replace('"BULLISH"', '"BULLISH",').replace("true", "True")
    # Actually let's make a simpler mangled version
    mangled = '{"label": "BULLISH", "score": 78, "confidence": 0.82, "tone": "Bullish", "summary": "test",}'
    obj, method = extract_and_repair_json(mangled)
    assert obj is not None
    assert method == "repaired"
    assert obj["label"] == "BULLISH"
    assert obj["score"] == 78


def test_malformed_json_graceful_fallback():
    """Completely invalid output should return None."""
    raw = "This is not JSON at all, just random text about markets."
    obj, method = extract_and_repair_json(raw)
    assert obj is None
    assert method is None


def test_json_with_think_tags_repaired():
    """<think> tags wrapping valid JSON should be stripped during repair."""
    raw = '<think>Let me analyze this carefully...</think>{"label": "MIXED", "score": 50}'
    obj, method = extract_and_repair_json(raw)
    assert obj is not None
    assert obj["label"] == "MIXED"
    assert obj["score"] == 50


def test_json_with_markdown_fences():
    raw = '```json\n' + json.dumps(MIXED_FIXTURE) + '\n```'
    obj, method = extract_and_repair_json(raw)
    assert obj is not None
    assert obj["label"] == "MIXED"


# ─── Think Tag Stripping Tests ───────────────────────────────────────────

def test_strip_think_tags_basic():
    text = '<think>reasoning here</think>{"label": "BULLISH"}'
    result = _strip_think_tags(text)
    assert "<think>" not in result
    assert '{"label": "BULLISH"}' == result


def test_strip_think_tags_multiline():
    text = (
        '<think>\nLet me think about this...\n'
        'The headlines suggest bullish sentiment.\n'
        'Score should be around 75.\n'
        '</think>\n{"label": "BULLISH", "score": 75}'
    )
    result = _strip_think_tags(text)
    assert "<think>" not in result
    assert "BULLISH" in result


def test_strip_think_tags_unclosed():
    text = '<think>reasoning that never ends...\n{"label": "BULLISH"}'
    result = _strip_think_tags(text)
    assert "<think>" not in result
    assert result == ""  # unclosed think eats everything


def test_strip_scratchpad_tags():
    text = '<scratchpad>notes here</scratchpad>{"score": 80}'
    result = _strip_think_tags(text)
    assert "<scratchpad>" not in result
    assert '{"score": 80}' == result


def test_strip_think_tags_empty():
    assert _strip_think_tags("") == ""
    assert _strip_think_tags(None) is None


def test_strip_think_tags_no_tags():
    text = '{"label": "NEUTRAL", "score": 50}'
    assert _strip_think_tags(text) == text


# ─── Full Pipeline Integration ───────────────────────────────────────────

def test_full_pipeline_bullish():
    """Simulate full pipeline: raw JSON → coerce → verify all fields."""
    raw = json.dumps(BULLISH_FIXTURE)
    obj, method = extract_and_repair_json(raw)
    assert obj is not None

    result = _coerce_news_sentiment_model_output(obj)
    assert result is not None
    assert result["label"] == "BULLISH"
    assert result["score"] == 78
    assert result["confidence"] == 0.82
    assert result["summary"] is not None
    assert result["headline_drivers"] is not None
    assert result["score_drivers"] is not None
    assert result["market_implications"] is not None
    assert result["trader_takeaway"] is not None


def test_full_pipeline_with_think_prefix():
    """Full pipeline with <think> prefix stripped before parse."""
    raw = '<think>analyzing...</think>\n' + json.dumps(BEARISH_FIXTURE)
    cleaned = _strip_think_tags(raw)
    obj, method = extract_and_repair_json(cleaned)
    assert obj is not None

    result = _coerce_news_sentiment_model_output(obj)
    assert result is not None
    assert result["label"] == "RISK-OFF"
    assert result["score"] == 18


# ─── Service-level error flow tests ─────────────────────────────────────

from unittest.mock import patch, MagicMock
import requests


class TestServiceRunModelAnalysis:
    """Tests for NewsSentimentService._run_model_analysis error flow."""

    def _make_service(self):
        """Build a minimal service with mocked deps."""
        from app.services.news_sentiment_service import NewsSentimentService
        settings = MagicMock()
        settings.MODEL_TIMEOUT_SECONDS = 90
        http_client = MagicMock()
        cache = MagicMock()
        return NewsSentimentService(
            settings=settings,
            http_client=http_client,
            cache=cache,
        )

    def test_success_returns_model_analysis(self):
        service = self._make_service()
        with patch(
            "common.model_analysis.analyze_news_sentiment",
            return_value=BULLISH_FIXTURE,
        ):
            result = service._run_model_analysis(
                [{"headline": "test", "source": "test"}],
                {"vix": 15.0},
            )
        assert result["model_analysis"] is not None
        assert result["model_analysis"]["label"] == "BULLISH"
        assert "error" not in result

    def test_timeout_returns_error_info(self):
        service = self._make_service()
        with patch(
            "common.model_analysis.analyze_news_sentiment",
            side_effect=requests.exceptions.ReadTimeout("timed out"),
        ):
            result = service._run_model_analysis(
                [{"headline": "test"}], {"vix": 15.0},
            )
        assert result["model_analysis"] is None
        assert result["error"]["kind"] == "timeout"
        assert "timed out" in result["error"]["message"].lower()

    def test_connection_error_returns_unreachable(self):
        service = self._make_service()
        with patch(
            "common.model_analysis.analyze_news_sentiment",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            result = service._run_model_analysis(
                [{"headline": "test"}], {"vix": 15.0},
            )
        assert result["model_analysis"] is None
        assert result["error"]["kind"] == "unreachable"

    def test_empty_items_returns_error(self):
        service = self._make_service()
        result = service._run_model_analysis([], {"vix": 15.0})
        assert result["model_analysis"] is None
        assert result["error"]["kind"] == "empty_response"
        assert "no news" in result["error"]["message"].lower()

    def test_generic_exception_returns_unknown(self):
        service = self._make_service()
        with patch(
            "common.model_analysis.analyze_news_sentiment",
            side_effect=RuntimeError("something unexpected"),
        ):
            result = service._run_model_analysis(
                [{"headline": "test"}], {"vix": 15.0},
            )
        assert result["model_analysis"] is None
        assert result["error"]["kind"] == "unknown"

    def test_schema_mismatch_returns_error_info(self):
        service = self._make_service()
        with patch(
            "common.model_analysis.analyze_news_sentiment",
            side_effect=ValueError("schema validation failed for field X"),
        ):
            result = service._run_model_analysis(
                [{"headline": "test"}], {"vix": 15.0},
            )
        assert result["model_analysis"] is None
        assert result["error"]["kind"] == "schema_mismatch"

    def test_slow_success_not_classified_as_timeout(self):
        """A model call that takes a long time but succeeds must return data, not a timeout error."""
        import time

        def slow_model(**kwargs):
            time.sleep(0.05)  # simulate slow but successful response
            return BULLISH_FIXTURE

        service = self._make_service()
        with patch(
            "common.model_analysis.analyze_news_sentiment",
            side_effect=slow_model,
        ):
            result = service._run_model_analysis(
                [{"headline": "test", "source": "test"}],
                {"vix": 15.0},
            )
        assert result["model_analysis"] is not None
        assert result["model_analysis"]["label"] == "BULLISH"
        assert "error" not in result

    def test_large_response_not_classified_as_timeout(self):
        """A large model response should parse successfully, not hang."""
        large_fixture = dict(BULLISH_FIXTURE)
        large_fixture["major_headlines"] = [
            {
                "headline": f"Headline {i} about market conditions",
                "category": "macro",
                "market_impact": "bullish",
                "why_it_matters": f"Reason {i} is important for traders.",
            }
            for i in range(50)
        ]

        service = self._make_service()
        with patch(
            "common.model_analysis.analyze_news_sentiment",
            return_value=large_fixture,
        ):
            result = service._run_model_analysis(
                [{"headline": "test"}], {"vix": 15.0},
            )
        assert result["model_analysis"] is not None
        assert len(result["model_analysis"].get("major_headlines", [])) == 50
        assert "error" not in result

    def test_parse_failure_classified_as_schema_not_timeout(self):
        """Backend parse failure must produce a parse/schema error, not timeout."""
        service = self._make_service()
        with patch(
            "common.model_analysis.analyze_news_sentiment",
            side_effect=ValueError("Model returned invalid news sentiment payload"),
        ):
            result = service._run_model_analysis(
                [{"headline": "test"}], {"vix": 15.0},
            )
        assert result["model_analysis"] is None
        assert result["error"]["kind"] in ("schema_mismatch", "malformed_response")
        assert "timeout" not in result["error"]["message"].lower()


if __name__ == "__main__":
    tests = [
        test_bullish_coercion,
        test_bearish_coercion,
        test_mixed_coercion,
        test_legacy_schema_backward_compat,
        test_score_bounds,
        test_invalid_label_defaults,
        test_none_input,
        test_malformed_json_repair_success,
        test_malformed_json_graceful_fallback,
        test_json_with_think_tags_repaired,
        test_json_with_markdown_fences,
        test_strip_think_tags_basic,
        test_strip_think_tags_multiline,
        test_strip_think_tags_unclosed,
        test_strip_scratchpad_tags,
        test_strip_think_tags_empty,
        test_strip_think_tags_no_tags,
        test_full_pipeline_bullish,
        test_full_pipeline_with_think_prefix,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")
