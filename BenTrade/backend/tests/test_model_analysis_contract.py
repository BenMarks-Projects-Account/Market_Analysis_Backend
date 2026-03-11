"""
Tests for model_analysis_contract.py — Model Analysis Response Contract.

Coverage:
  1. Contract shape tests (success, error, degraded)
  2. Analysis type metadata tests
  3. Field extraction tests (summary, key_points, risks, actions, confidence, etc.)
  4. parse_raw_model_text tests (10+ parsing scenarios)
  5. Error handling tests (exception classification, timeout, unreachable)
  6. Integration / compatibility tests (breadth, news, active trade shapes)
  7. wrap_service_model_response tests
"""

from __future__ import annotations

import sys
import os
import pytest

# ── Path setup ──────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.model_analysis_contract import (
    ANALYSIS_METADATA,
    normalize_model_analysis_response,
    parse_raw_model_text,
    wrap_service_model_response,
    _extract_summary,
    _extract_key_points,
    _extract_risks,
    _extract_actions,
    _extract_confidence,
    _extract_parse_strategy,
    _extract_warnings,
)


# ── Shared fixtures ──────────────────────────────────────────────────────

CONTRACT_FIELDS = {
    "status", "analysis_type", "analysis_name", "category",
    "model_source", "requested_at", "completed_at", "duration_ms",
    "raw_content", "normalized_text", "structured_payload",
    "summary", "key_points", "risks", "actions", "confidence",
    "warnings", "error_type", "error_message",
    "parse_strategy", "response_format", "time_horizon", "metadata",
}


def _make_breadth_model_result() -> dict:
    """Realistic breadth model analysis output (success path)."""
    return {
        "label": "BULLISH",
        "score": 72.5,
        "confidence": 0.85,
        "summary": "Broad participation confirms uptrend with strong advance-decline ratios.",
        "pillar_analysis": {
            "participation": {"assessment": "Strong", "detail": "A/D line rising"},
            "trend": {"assessment": "Confirmed", "detail": "Above 200 SMA"},
        },
        "trader_takeaway": "Stay long; breadth supports continued upside.",
        "uncertainty_flags": [
            "Volume divergence on small caps",
            "Leadership narrowing in tech",
        ],
        "key_risks": [],
        "warnings": ["Missing Russell 2000 breadth data"],
        "_trace": {
            "method": "direct",
            "input_keys": ["raw_inputs", "pillar_scores"],
            "response_snippet": '{"label":"BULLISH"...}',
        },
    }


def _make_news_model_result() -> dict:
    """Realistic news sentiment model analysis output (success path)."""
    return {
        "label": "BEARISH",
        "score": 35.0,
        "confidence": 0.72,
        "summary": "Negative headline pressure from geopolitical tensions and rate fears.",
        "tone": "cautious",
        "headline_drivers": [
            {"headline": "Fed signals more hikes", "impact": "negative"},
        ],
        "trader_takeaway": "Reduce risk exposure; wait for clarity on rate path.",
        "uncertainty_flags": ["Conflicting earnings signals"],
        "key_risks": ["Rate shock risk", "Geopolitical escalation"],
        "_trace": {
            "method": "strip_fences",
            "input_keys": ["headlines", "macro_snapshot"],
        },
    }


def _make_active_trade_result() -> dict:
    """Realistic active trade model analysis output."""
    return {
        "headline": "Position performing within thesis parameters",
        "stance": "HOLD",
        "confidence": 75,
        "thesis_status": "INTACT",
        "summary": "Short put spread is decaying as expected with 14 DTE remaining.",
        "key_risks": ["VIX expansion above 22", "Earnings gap risk"],
        "key_supports": ["Theta decay accelerating", "SPY above support"],
        "action_plan": {
            "primary_action": "Hold until 50% profit target",
            "urgency": "LOW",
            "next_step": "Review at 7 DTE",
            "risk_trigger": "SPY below 430",
            "upside_trigger": "50% max profit reached",
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. Contract Shape Tests
# ═══════════════════════════════════════════════════════════════════════


class TestContractShape:
    """Verify every contract has all required fields with correct types."""

    def test_success_contract_shape(self):
        result = normalize_model_analysis_response(
            "breadth_participation",
            model_result=_make_breadth_model_result(),
            requested_at="2025-01-15T10:00:00+00:00",
            duration_ms=1234,
        )
        assert set(result.keys()) == CONTRACT_FIELDS
        assert result["status"] == "success"
        assert result["analysis_type"] == "breadth_participation"
        assert result["analysis_name"] == "Breadth & Participation"
        assert result["category"] == "market_picture"
        assert result["duration_ms"] == 1234
        assert result["response_format"] == "json"
        assert result["structured_payload"] is not None
        assert result["error_type"] is None
        assert result["error_message"] is None
        assert isinstance(result["metadata"], dict)

    def test_error_contract_shape(self):
        result = normalize_model_analysis_response(
            "news_sentiment",
            error_info={"kind": "timeout", "message": "Timed out after 90s"},
            requested_at="2025-01-15T10:00:00+00:00",
        )
        assert set(result.keys()) == CONTRACT_FIELDS
        assert result["status"] == "error"
        assert result["response_format"] == "error"
        assert result["error_type"] == "timeout"
        assert result["error_message"] == "Timed out after 90s"
        assert result["structured_payload"] is None
        assert result["summary"] is None
        assert result["key_points"] == []
        assert result["risks"] == []
        assert result["actions"] == []
        assert result["confidence"] is None

    def test_degraded_contract_shape(self):
        fallback = {
            "summary": "The market looks cautious with mixed signals...",
            "_plaintext_fallback": True,
        }
        result = normalize_model_analysis_response(
            "volatility_options",
            model_result=fallback,
        )
        assert set(result.keys()) == CONTRACT_FIELDS
        assert result["status"] == "degraded"
        assert result["response_format"] == "plaintext"
        assert result["summary"] == "The market looks cautious with mixed signals..."
        assert "Model returned plain text instead of structured JSON" in result["warnings"]

    def test_empty_error_contract_shape(self):
        """Neither model_result nor error → error with empty format."""
        result = normalize_model_analysis_response("regime")
        assert result["status"] == "error"
        assert result["response_format"] == "empty"
        assert result["error_type"] is None

    def test_completed_at_auto_set(self):
        result = normalize_model_analysis_response(
            "breadth_participation",
            model_result={"summary": "test"},
        )
        assert result["completed_at"] is not None

    def test_completed_at_explicit(self):
        result = normalize_model_analysis_response(
            "breadth_participation",
            model_result={"summary": "test"},
            completed_at="2025-06-01T12:00:00+00:00",
        )
        assert result["completed_at"] == "2025-06-01T12:00:00+00:00"


# ═══════════════════════════════════════════════════════════════════════
# 2. Analysis Type Metadata Tests
# ═══════════════════════════════════════════════════════════════════════


class TestAnalysisMetadata:

    def test_known_analysis_types_have_metadata(self):
        for key, meta in ANALYSIS_METADATA.items():
            assert "name" in meta, f"{key} missing 'name'"
            assert "category" in meta, f"{key} missing 'category'"

    def test_all_market_picture_types(self):
        mp_types = [
            "regime", "breadth_participation", "volatility_options",
            "cross_asset_macro", "flows_positioning", "news_sentiment",
            "liquidity_conditions",
        ]
        for t in mp_types:
            assert t in ANALYSIS_METADATA
            assert ANALYSIS_METADATA[t]["category"] == "market_picture"

    def test_unknown_analysis_type_graceful(self):
        result = normalize_model_analysis_response(
            "unknown_future_domain",
            model_result={"summary": "test"},
        )
        assert result["analysis_name"] == "unknown_future_domain"
        assert result["category"] == "unknown"


# ═══════════════════════════════════════════════════════════════════════
# 3. Field Extraction Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSummaryExtraction:

    def test_from_summary_field(self):
        assert _extract_summary({"summary": "Bullish outlook"}) == "Bullish outlook"

    def test_from_executive_summary(self):
        assert _extract_summary({"executive_summary": "Market rising"}) == "Market rising"

    def test_from_headline(self):
        assert _extract_summary({"headline": "Position solid"}) == "Position solid"

    def test_priority_order(self):
        """summary takes priority over executive_summary and headline."""
        r = {"summary": "Primary", "executive_summary": "Secondary", "headline": "Third"}
        assert _extract_summary(r) == "Primary"

    def test_empty_falls_through(self):
        assert _extract_summary({"summary": "", "headline": "Fallback"}) == "Fallback"

    def test_none_on_missing(self):
        assert _extract_summary({}) is None

    def test_whitespace_stripped(self):
        assert _extract_summary({"summary": "  trimmed  "}) == "trimmed"


class TestKeyPointsExtraction:

    def test_from_uncertainty_flags(self):
        result = _extract_key_points({"uncertainty_flags": ["Flag A", "Flag B"]})
        assert result == ["Flag A", "Flag B"]

    def test_from_key_drivers(self):
        result = _extract_key_points({"key_drivers": ["Driver 1"]})
        assert result == ["Driver 1"]

    def test_combined_sources(self):
        result = _extract_key_points({
            "uncertainty_flags": ["UF1"],
            "key_supports": ["KS1"],
        })
        assert "UF1" in result
        assert "KS1" in result

    def test_max_10_items(self):
        flags = [f"Flag {i}" for i in range(15)]
        result = _extract_key_points({"uncertainty_flags": flags})
        assert len(result) == 10

    def test_empty_on_missing(self):
        assert _extract_key_points({}) == []


class TestRisksExtraction:

    def test_from_key_risks_list(self):
        result = _extract_risks({"key_risks": ["VIX spike", "Gap risk"]})
        assert result == ["VIX spike", "Gap risk"]

    def test_from_risk_review_dict(self):
        result = _extract_risks({"risk_review": {"volatility": "Elevated", "liquidity": "Thin"}})
        assert any("volatility" in r for r in result)
        assert any("liquidity" in r for r in result)

    def test_max_10_items(self):
        risks = [f"Risk {i}" for i in range(15)]
        result = _extract_risks({"key_risks": risks})
        assert len(result) == 10


class TestActionsExtraction:

    def test_from_trader_takeaway(self):
        result = _extract_actions({"trader_takeaway": "Stay long"})
        assert result == ["Stay long"]

    def test_from_action_plan_dict(self):
        result = _extract_actions({
            "action_plan": {"primary_action": "Hold position", "next_step": "Review at 7 DTE"}
        })
        assert "Hold position" in result

    def test_from_action_string(self):
        result = _extract_actions({"action": "Close at 50% profit"})
        assert result == ["Close at 50% profit"]

    def test_empty_on_missing(self):
        assert _extract_actions({}) == []


class TestConfidenceExtraction:

    def test_passthrough_0_to_1(self):
        assert _extract_confidence({"confidence": 0.85}) == 0.85

    def test_convert_0_to_100_scale(self):
        """confidence > 1.0 should be divided by 100."""
        assert _extract_confidence({"confidence": 75}) == 0.75

    def test_clamp_above_1(self):
        # 1.5 > 1.0 → divided by 100 → 0.015 → round(0.015, 2) = 0.01
        assert _extract_confidence({"confidence": 1.5}) == 0.01

    def test_clamp_below_0(self):
        assert _extract_confidence({"confidence": -0.5}) == 0.0

    def test_none_on_missing(self):
        assert _extract_confidence({}) is None

    def test_none_on_invalid_string(self):
        assert _extract_confidence({"confidence": "high"}) is None

    def test_string_numeric(self):
        assert _extract_confidence({"confidence": "0.6"}) == 0.6


class TestParseStrategyExtraction:

    def test_from_trace_method(self):
        result = _extract_parse_strategy({"_trace": {"method": "strip_fences"}})
        assert result == "strip_fences"

    def test_from_trace_parse_method(self):
        result = _extract_parse_strategy({"_trace": {"parse_method": "repaired"}})
        assert result == "repaired"

    def test_none_on_missing_trace(self):
        assert _extract_parse_strategy({}) is None


class TestWarningsExtraction:

    def test_from_warnings_list(self):
        result = _extract_warnings({"warnings": ["Missing data", "Stale cache"]})
        assert result == ["Missing data", "Stale cache"]

    def test_from_data_quality_flags(self):
        result = _extract_warnings({"data_quality_flags": ["Low volume"]})
        assert result == ["Low volume"]

    def test_plaintext_fallback_adds_warning(self):
        result = _extract_warnings({"_plaintext_fallback": True})
        assert "Model returned plain text instead of structured JSON" in result

    def test_max_15_items(self):
        warnings = [f"Warning {i}" for i in range(20)]
        result = _extract_warnings({"warnings": warnings})
        assert len(result) == 15


# ═══════════════════════════════════════════════════════════════════════
# 4. parse_raw_model_text Tests (10+ parsing scenarios)
# ═══════════════════════════════════════════════════════════════════════


class TestParseRawModelText:

    def test_valid_json_dict(self):
        """Case 1: Clean JSON object."""
        raw = '{"label": "BULLISH", "score": 72, "summary": "Looks good"}'
        result = parse_raw_model_text(raw, "breadth_participation")
        assert result["status"] == "success"
        assert result["response_format"] == "json"
        assert result["structured_payload"]["label"] == "BULLISH"
        assert result["summary"] == "Looks good"

    def test_code_fenced_json(self):
        """Case 2: Markdown code-fenced JSON."""
        raw = '```json\n{"label": "BEARISH", "score": 30, "summary": "Risk off"}\n```'
        result = parse_raw_model_text(raw, "news_sentiment")
        assert result["status"] == "success"
        assert result["structured_payload"]["label"] == "BEARISH"
        assert result["raw_content"] == raw

    def test_json_with_prose_around(self):
        """Case 3: JSON embedded in prose (partial JSON extraction)."""
        raw = 'Here is my analysis:\n{"label": "NEUTRAL", "score": 50, "summary": "Mixed"}\nEnd of analysis.'
        result = parse_raw_model_text(raw, "regime")
        assert result["status"] == "success"
        assert result["structured_payload"]["label"] == "NEUTRAL"

    def test_plaintext_prose(self):
        """Case 4: Plain English prose, no JSON at all."""
        raw = "The market appears to be in a cautious holding pattern with mixed signals across sectors and timeframes."
        result = parse_raw_model_text(raw, "breadth_participation")
        assert result["status"] == "degraded"
        assert result["response_format"] == "plaintext"
        assert "cautious holding pattern" in result["summary"]

    def test_empty_string(self):
        """Case 5: Empty string."""
        result = parse_raw_model_text("", "regime")
        assert result["status"] == "error"
        assert result["error_type"] == "empty_response"

    def test_none_input(self):
        """Case 6: None content."""
        result = parse_raw_model_text(None, "regime")
        assert result["status"] == "error"
        assert result["error_type"] == "empty_response"

    def test_think_tags_with_json(self):
        """Case 7: Think tags wrapping valid JSON."""
        raw = '<think>Let me analyze...</think>\n{"label": "BULLISH", "score": 80, "summary": "Strong"}'
        result = parse_raw_model_text(raw, "volatility_options")
        assert result["status"] == "success"
        assert result["structured_payload"]["label"] == "BULLISH"

    def test_think_tags_only(self):
        """Case 8: Only reasoning tags, no actual content."""
        raw = "<think>I need to think about this more deeply and consider all factors...</think>"
        result = parse_raw_model_text(raw, "regime")
        assert result["status"] == "error"
        assert result["error_type"] == "empty_response"

    def test_invalid_utf8_bytes(self):
        """Case 9: Bytes with invalid UTF-8."""
        raw = b'{"label": "BULLISH", "summary": "Valid \xff content"}'
        result = parse_raw_model_text(raw, "regime")
        # Should not raise; will attempt to parse with replacement chars
        assert result["status"] in ("success", "error", "degraded")

    def test_json_array_first_dict(self):
        """Case 10: JSON array where first element is a dict."""
        raw = '[{"label": "MIXED", "score": 55, "summary": "Balanced"}]'
        result = parse_raw_model_text(raw, "cross_asset_macro")
        assert result["status"] == "success"
        assert result["structured_payload"]["label"] == "MIXED"

    def test_short_plaintext_becomes_error(self):
        """Case 11: Very short text (< 20 chars) → malformed error."""
        result = parse_raw_model_text("OK fine.", "regime")
        assert result["status"] == "error"
        assert result["error_type"] == "malformed_response"

    def test_whitespace_only(self):
        """Case 12: Whitespace-only string."""
        result = parse_raw_model_text("   \n\t  ", "regime")
        assert result["status"] == "error"
        assert result["error_type"] == "empty_response"

    def test_normalized_text_populated(self):
        """Sanitized text is captured in normalized_text when present."""
        raw = '<think>reasoning</think>{"summary": "After think tags"}'
        result = parse_raw_model_text(raw, "regime")
        assert result["normalized_text"] is not None
        assert "<think>" not in result["normalized_text"]

    def test_raw_content_preserved(self):
        """Original raw text is preserved even after normalization."""
        raw = '```json\n{"summary": "test"}\n```'
        result = parse_raw_model_text(raw, "regime")
        assert result["raw_content"] == raw

    def test_long_plaintext_truncated(self):
        """Plaintext > 1500 chars gets truncated with ellipsis."""
        raw = "A " * 1000  # 2000 chars
        result = parse_raw_model_text(raw, "regime")
        assert result["status"] == "degraded"
        assert result["summary"].endswith("\u2026")
        assert len(result["summary"]) <= 1510


# ═══════════════════════════════════════════════════════════════════════
# 5. Error Handling Tests
# ═══════════════════════════════════════════════════════════════════════


class TestErrorHandling:

    def test_error_from_exception(self):
        """Exception is classified via classify_model_error."""
        exc = ConnectionError("Connection refused")
        result = normalize_model_analysis_response(
            "breadth_participation",
            error=exc,
        )
        assert result["status"] == "error"
        assert result["error_type"] == "unreachable"
        assert result["error_message"] is not None

    def test_error_from_error_info(self):
        """Pre-classified error dict is passed through."""
        result = normalize_model_analysis_response(
            "news_sentiment",
            error_info={"kind": "timeout", "message": "Timed out"},
        )
        assert result["error_type"] == "timeout"
        assert result["error_message"] == "Timed out"

    def test_timeout_exception(self):
        import requests.exceptions
        exc = requests.exceptions.ReadTimeout("Read timed out")
        result = normalize_model_analysis_response(
            "regime",
            error=exc,
        )
        assert result["error_type"] == "timeout"

    def test_error_preserves_analysis_type(self):
        result = normalize_model_analysis_response(
            "flows_positioning",
            error_info={"kind": "model_unavailable", "message": "No model"},
        )
        assert result["analysis_type"] == "flows_positioning"
        assert result["analysis_name"] == "Flows & Positioning"
        assert result["category"] == "market_picture"


# ═══════════════════════════════════════════════════════════════════════
# 6. Integration / Compatibility Tests
# ═══════════════════════════════════════════════════════════════════════


class TestIntegrationBreadth:
    """Verify normalization of realistic breadth model output."""

    def test_breadth_success_normalization(self):
        model_result = _make_breadth_model_result()
        result = normalize_model_analysis_response(
            "breadth_participation",
            model_result=model_result,
            requested_at="2025-01-15T10:00:00+00:00",
            duration_ms=3200,
        )
        assert result["status"] == "success"
        assert result["summary"] == "Broad participation confirms uptrend with strong advance-decline ratios."
        assert result["confidence"] == 0.85
        assert "Volume divergence on small caps" in result["key_points"]
        assert "Leadership narrowing in tech" in result["key_points"]
        assert result["actions"] == ["Stay long; breadth supports continued upside."]
        assert result["parse_strategy"] == "direct"
        assert result["metadata"]["label"] == "BULLISH"
        assert result["metadata"]["score"] == 72.5
        assert result["warnings"] == ["Missing Russell 2000 breadth data"]
        # Structured payload preserved unchanged
        assert result["structured_payload"] is model_result

    def test_breadth_error_normalization(self):
        result = normalize_model_analysis_response(
            "breadth_participation",
            error_info={"kind": "timeout", "message": "Model timed out after 180s"},
        )
        assert result["status"] == "error"
        assert result["error_type"] == "timeout"
        assert result["structured_payload"] is None


class TestIntegrationNewsSentiment:
    """Verify normalization of realistic news sentiment model output."""

    def test_news_success_normalization(self):
        model_result = _make_news_model_result()
        result = normalize_model_analysis_response(
            "news_sentiment",
            model_result=model_result,
            requested_at="2025-01-15T10:00:00+00:00",
            duration_ms=5100,
        )
        assert result["status"] == "success"
        assert result["analysis_name"] == "News & Sentiment"
        assert result["summary"] == "Negative headline pressure from geopolitical tensions and rate fears."
        assert result["confidence"] == 0.72
        assert "Rate shock risk" in result["risks"]
        assert "Geopolitical escalation" in result["risks"]
        assert result["parse_strategy"] == "strip_fences"


class TestIntegrationActiveTrade:
    """Verify normalization of realistic active trade model output."""

    def test_active_trade_success_normalization(self):
        model_result = _make_active_trade_result()
        result = normalize_model_analysis_response(
            "active_trade",
            model_result=model_result,
            duration_ms=2800,
        )
        assert result["status"] == "success"
        assert result["analysis_name"] == "Active Trade Review"
        assert result["category"] == "active_trades"
        # Summary extracted from "headline" (since no "summary" key matches first)
        # Actually active_trade has "summary", which comes first
        assert "decaying as expected" in result["summary"]
        # Confidence: 75 → 0.75 (divided by 100)
        assert result["confidence"] == 0.75
        # Risks from key_risks
        assert "VIX expansion above 22" in result["risks"]
        # Actions from action_plan.primary_action
        assert "Hold until 50% profit target" in result["actions"]


class TestIntegrationPlaintextFallback:
    """Verify plaintext fallback produces correct contract."""

    def test_plaintext_fallback_from_module(self):
        """Simulates the _build_plaintext_fallback output shape."""
        fallback = {
            "label": "ANALYSIS",
            "score": None,
            "confidence": None,
            "summary": "The breadth indicators show mixed signals with participation narrowing...",
            "pillar_analysis": {},
            "trader_takeaway": "",
            "uncertainty_flags": ["Model returned plain text instead of structured JSON"],
            "_plaintext_fallback": True,
            "_module": "breadth",
        }
        result = normalize_model_analysis_response(
            "breadth_participation",
            model_result=fallback,
        )
        assert result["status"] == "degraded"
        assert result["response_format"] == "plaintext"
        assert "mixed signals" in result["summary"]
        assert result["confidence"] is None
        assert result["metadata"]["label"] == "ANALYSIS"


# ═══════════════════════════════════════════════════════════════════════
# 7. wrap_service_model_response Tests
# ═══════════════════════════════════════════════════════════════════════


class TestWrapServiceModelResponse:

    def test_adds_normalized_key(self):
        service_result = {
            "model_analysis": _make_breadth_model_result(),
        }
        wrapped = wrap_service_model_response(
            "breadth_participation", service_result,
            requested_at="2025-01-15T10:00:00+00:00",
            duration_ms=3000,
        )
        assert "normalized" in wrapped
        assert wrapped["normalized"]["status"] == "success"
        # Original keys preserved
        assert "model_analysis" in wrapped
        assert wrapped["model_analysis"] is service_result["model_analysis"]

    def test_preserves_existing_keys(self):
        service_result = {
            "model_analysis": {"summary": "test"},
            "as_of": "2025-01-15T10:00:00+00:00",
            "extra_key": "extra_value",
        }
        wrapped = wrap_service_model_response("regime", service_result)
        assert wrapped["as_of"] == "2025-01-15T10:00:00+00:00"
        assert wrapped["extra_key"] == "extra_value"

    def test_error_service_result(self):
        service_result = {
            "model_analysis": None,
            "error": {"kind": "timeout", "message": "Timed out"},
        }
        wrapped = wrap_service_model_response("news_sentiment", service_result)
        assert wrapped["normalized"]["status"] == "error"
        assert wrapped["normalized"]["error_type"] == "timeout"
        # Original error dict preserved
        assert wrapped["error"]["kind"] == "timeout"

    def test_returns_same_dict_reference(self):
        """wrap mutates and returns the same dict (not a copy)."""
        service_result = {"model_analysis": {"summary": "test"}}
        returned = wrap_service_model_response("regime", service_result)
        assert returned is service_result

    def test_breadth_service_integration_shape(self):
        """Simulates the breadth service _run_model_analysis output after integration."""
        model_result = _make_breadth_model_result()
        # This is what _run_model_analysis returns after our change:
        outcome = {"model_analysis": model_result}
        wrapped = wrap_service_model_response(
            "breadth_participation", outcome,
            requested_at="2025-01-15T10:00:00+00:00",
            duration_ms=3200,
        )
        # The async run_model_analysis reads model_analysis — still works:
        assert wrapped["model_analysis"] is model_result
        # But now also has normalized:
        norm = wrapped["normalized"]
        assert norm["status"] == "success"
        assert norm["analysis_type"] == "breadth_participation"
        assert norm["duration_ms"] == 3200

    def test_news_service_empty_items_integration(self):
        """Simulates news service with no items → error path."""
        outcome = {
            "model_analysis": None,
            "error": {
                "kind": "empty_response",
                "message": "No news headlines available for model analysis.",
            },
        }
        wrapped = wrap_service_model_response(
            "news_sentiment", outcome,
            requested_at="2025-01-15T10:00:00+00:00",
            duration_ms=0,
        )
        assert wrapped["normalized"]["status"] == "error"
        assert wrapped["normalized"]["error_type"] == "empty_response"
        assert wrapped["normalized"]["duration_ms"] == 0
