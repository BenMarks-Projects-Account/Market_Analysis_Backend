"""Tests for common/model_sanitize.py — centralized LLM response sanitization.

Covers:
  - sanitize_model_text: think/scratchpad/reasoning tag removal
  - had_think_tags: tag detection without modification
  - classify_model_error: exception → stable error kind mapping
  - user_facing_error_message: error kind → user message

Also covers the narrative memo helpers in routes_active_trades.py:
  - _coerce_narrative_memo: LLM dict  → normalised memo contract
  - _build_fallback_narrative: fallback structure for failed model calls
"""

import pytest
import requests
import httpx

from common.model_sanitize import (
    sanitize_model_text,
    had_think_tags,
    classify_model_error,
    user_facing_error_message,
)
from app.api.routes_active_trades import (
    _coerce_narrative_memo,
    _build_fallback_narrative,
    _sanitize_model_analysis,
    _build_fallback_analysis,
)


# ═══════════════════════════════════════════════════════════════
# sanitize_model_text
# ═══════════════════════════════════════════════════════════════

class TestSanitizeModelText:
    def test_none_returns_empty(self):
        assert sanitize_model_text(None) == ""

    def test_empty_returns_empty(self):
        assert sanitize_model_text("") == ""

    def test_no_tags_passthrough(self):
        txt = '{"label": "HOLD", "summary": "Looking good"}'
        assert sanitize_model_text(txt) == txt

    def test_strips_closed_think_block(self):
        raw = '<think>Let me reason about this...</think>{"label": "HOLD"}'
        assert sanitize_model_text(raw) == '{"label": "HOLD"}'

    def test_strips_multiline_think_block(self):
        raw = (
            "<think>\nStep 1: analyze...\nStep 2: decide...\n</think>\n"
            '{"summary": "All clear"}'
        )
        result = sanitize_model_text(raw)
        assert "<think>" not in result
        assert "Step 1" not in result
        assert '{"summary": "All clear"}' in result

    def test_strips_unclosed_think_block(self):
        raw = '{"label": "HOLD"}<think>model keeps going forever...'
        result = sanitize_model_text(raw)
        assert "<think>" not in result
        assert "keeps going" not in result
        assert '{"label": "HOLD"}' in result

    def test_strips_scratchpad_closed(self):
        raw = "<scratchpad>internal notes</scratchpad>actual content"
        result = sanitize_model_text(raw)
        assert "internal notes" not in result
        assert "actual content" in result

    def test_strips_scratchpad_unclosed(self):
        raw = "good stuff<scratchpad>leaked internal"
        assert sanitize_model_text(raw) == "good stuff"

    def test_strips_stray_tags(self):
        raw = "hello <reasoning> world </reasoning> done"
        result = sanitize_model_text(raw)
        assert "<reasoning>" not in result
        assert "</reasoning>" not in result
        assert "hello" in result and "world" in result and "done" in result

    def test_strips_mixed_tags(self):
        raw = (
            "<think>chain of thought</think>"
            "<scratchpad>notes</scratchpad>"
            "final answer"
        )
        result = sanitize_model_text(raw)
        assert result == "final answer"

    def test_stray_tags_stripped_content_preserved(self):
        """_STRAY_TAGS strips orphan tag elements but keeps text between them."""
        raw = "prefix <reasoning>middle</reasoning> suffix"
        result = sanitize_model_text(raw)
        assert "<reasoning>" not in result
        assert "middle" in result

    def test_case_insensitive(self):
        raw = "<THINK>reasoning</THINK>output"
        assert sanitize_model_text(raw) == "output"

    def test_think_with_json_inside_preserved(self):
        """Only the think block is removed; JSON after it stays."""
        raw = '<think>{"internal": true}</think>{"label": "HOLD"}'
        result = sanitize_model_text(raw)
        assert '{"label": "HOLD"}' in result
        assert '{"internal": true}' not in result


# ═══════════════════════════════════════════════════════════════
# had_think_tags
# ═══════════════════════════════════════════════════════════════

class TestHadThinkTags:
    def test_none_returns_false(self):
        assert had_think_tags(None) is False

    def test_empty_returns_false(self):
        assert had_think_tags("") is False

    def test_clean_text_returns_false(self):
        assert had_think_tags('{"label": "HOLD"}') is False

    def test_closed_think_detected(self):
        assert had_think_tags("<think>reasoning</think>output") is True

    def test_unclosed_think_detected(self):
        assert had_think_tags("output<think>trailing reasoning") is True

    def test_scratchpad_detected(self):
        assert had_think_tags("<scratchpad>notes</scratchpad>output") is True


# ═══════════════════════════════════════════════════════════════
# classify_model_error
# ═══════════════════════════════════════════════════════════════

class TestClassifyModelError:
    def test_none_returns_unknown(self):
        assert classify_model_error(None) == "unknown"

    # Timeout variants
    def test_requests_read_timeout(self):
        assert classify_model_error(requests.exceptions.ReadTimeout()) == "timeout"

    def test_requests_connect_timeout(self):
        assert classify_model_error(requests.exceptions.ConnectTimeout()) == "timeout"

    def test_httpx_read_timeout(self):
        assert classify_model_error(httpx.ReadTimeout("timeout")) == "timeout"

    def test_httpx_connect_timeout(self):
        assert classify_model_error(httpx.ConnectTimeout("timeout")) == "timeout"

    def test_generic_timeout_message(self):
        assert classify_model_error(Exception("request timed out")) == "timeout"

    # Unreachable
    def test_requests_connection_error(self):
        assert classify_model_error(requests.exceptions.ConnectionError()) == "unreachable"

    def test_httpx_connect_error(self):
        assert classify_model_error(httpx.ConnectError("refused")) == "unreachable"

    def test_generic_connection_refused(self):
        assert classify_model_error(Exception("connection refused")) == "unreachable"

    # Model unavailable
    def test_unavailable_message(self):
        assert classify_model_error(Exception("Model unavailable")) == "model_unavailable"

    def test_not_enabled_message(self):
        assert classify_model_error(Exception("Feature not enabled")) == "model_unavailable"

    # Parse / schema
    def test_json_decode_message(self):
        assert classify_model_error(Exception("JSON decode error")) == "parse_failure"

    def test_schema_validation(self):
        assert classify_model_error(Exception("schema validation failed")) == "schema_mismatch"

    def test_invalid_response(self):
        assert classify_model_error(Exception("invalid response payload")) == "malformed_response"

    def test_empty_response(self):
        assert classify_model_error(Exception("empty response body")) == "empty_response"

    def test_no_result(self):
        assert classify_model_error(Exception("no result returned")) == "empty_response"

    # Fallback
    def test_unrecognized_returns_unknown(self):
        assert classify_model_error(Exception("something bizarre")) == "unknown"


# ═══════════════════════════════════════════════════════════════
# user_facing_error_message
# ═══════════════════════════════════════════════════════════════

class TestUserFacingErrorMessage:
    def test_timeout_message(self):
        msg = user_facing_error_message("timeout")
        assert "timed out" in msg.lower()

    def test_unreachable_message(self):
        msg = user_facing_error_message("unreachable")
        assert "reach" in msg.lower() or "running" in msg.lower()

    def test_empty_response_message(self):
        msg = user_facing_error_message("empty_response")
        assert "empty" in msg.lower()

    def test_unknown_kind_returns_generic(self):
        msg = user_facing_error_message("unknown")
        assert msg  # non-empty

    def test_invalid_kind_returns_generic(self):
        msg = user_facing_error_message("not_a_real_kind")
        assert msg  # non-empty, fallback

    def test_custom_timeout_seconds(self):
        msg = user_facing_error_message("timeout", timeout_seconds=120)
        assert "120" in msg


# ═══════════════════════════════════════════════════════════════
# _coerce_narrative_memo
# ═══════════════════════════════════════════════════════════════

class TestCoerceNarrativeMemo:
    def test_valid_full_memo(self):
        parsed = {
            "label": "HOLD",
            "summary": "Position looks stable.",
            "thesis_check": "Thesis intact, no change.",
            "key_risks": ["Time decay", "Gap risk"],
            "action": "Continue to hold.",
            "confidence": 75,
        }
        result = _coerce_narrative_memo(parsed)
        assert result is not None
        assert result["label"] == "HOLD"
        assert result["summary"] == "Position looks stable."
        assert result["confidence"] == 75
        assert len(result["key_risks"]) == 2

    def test_missing_summary_returns_none(self):
        parsed = {"label": "HOLD", "thesis_check": "ok", "confidence": 50}
        assert _coerce_narrative_memo(parsed) is None

    def test_non_dict_returns_none(self):
        assert _coerce_narrative_memo("just a string") is None
        assert _coerce_narrative_memo(None) is None
        assert _coerce_narrative_memo([1, 2]) is None

    def test_unknown_label_defaults_to_hold(self):
        parsed = {"label": "YOLO", "summary": "something"}
        result = _coerce_narrative_memo(parsed)
        assert result["label"] == "HOLD"

    def test_missing_label_defaults_to_hold(self):
        parsed = {"summary": "some text"}
        result = _coerce_narrative_memo(parsed)
        assert result["label"] == "HOLD"

    def test_label_normalised_uppercase(self):
        parsed = {"label": "hold", "summary": "ok"}
        result = _coerce_narrative_memo(parsed)
        assert result["label"] == "HOLD"

    def test_confidence_clamped_high(self):
        parsed = {"summary": "ok", "confidence": 200}
        result = _coerce_narrative_memo(parsed)
        assert result["confidence"] == 100

    def test_confidence_clamped_low(self):
        parsed = {"summary": "ok", "confidence": -10}
        result = _coerce_narrative_memo(parsed)
        assert result["confidence"] == 0

    def test_confidence_float_converted(self):
        parsed = {"summary": "ok", "confidence": 72.8}
        result = _coerce_narrative_memo(parsed)
        assert result["confidence"] == 72

    def test_confidence_invalid_defaults_50(self):
        parsed = {"summary": "ok", "confidence": "high"}
        result = _coerce_narrative_memo(parsed)
        assert result["confidence"] == 50

    def test_risks_truncated_to_five(self):
        parsed = {"summary": "ok", "key_risks": ["a", "b", "c", "d", "e", "f", "g"]}
        result = _coerce_narrative_memo(parsed)
        assert len(result["key_risks"]) == 5

    def test_risks_string_wrapped_in_list(self):
        parsed = {"summary": "ok", "key_risks": "single risk"}
        result = _coerce_narrative_memo(parsed)
        assert result["key_risks"] == ["single risk"]

    def test_empty_risks_handled(self):
        parsed = {"summary": "ok", "key_risks": []}
        result = _coerce_narrative_memo(parsed)
        assert result["key_risks"] == []

    def test_risks_none_handled(self):
        parsed = {"summary": "ok"}
        result = _coerce_narrative_memo(parsed)
        assert result["key_risks"] == []


# ═══════════════════════════════════════════════════════════════
# _build_fallback_narrative
# ═══════════════════════════════════════════════════════════════

class TestBuildFallbackNarrative:
    def test_structure_complete(self):
        result = _build_fallback_narrative("WINNING", 85)
        assert result["label"] == "HOLD"
        assert "WINNING" in result["summary"]
        assert "85" in result["summary"]
        assert result["confidence"] == 0
        assert isinstance(result["key_risks"], list)
        assert result["action"]
        assert result["thesis_check"]

    def test_different_status(self):
        result = _build_fallback_narrative("LOSING", 20)
        assert "LOSING" in result["summary"]
        assert "20" in result["summary"]


# ═══════════════════════════════════════════════════════════════
# Integration: sanitize → parse → coerce pipeline
# ═══════════════════════════════════════════════════════════════

class TestSanitizeAndCoercePipeline:
    """End-to-end: raw LLM output with think tags → sanitize → JSON parse → coerce."""

    def test_think_block_then_valid_json(self):
        import json
        raw = (
            '<think>Let me analyze the position...\n'
            'Step 1: check P&L\nStep 2: evaluate thesis</think>\n'
            '{"label": "HOLD", "summary": "Position is fine.", '
            '"thesis_check": "Thesis intact", '
            '"key_risks": ["Time decay"], "action": "Hold", "confidence": 80}'
        )
        cleaned = sanitize_model_text(raw)
        assert "<think>" not in cleaned
        parsed = json.loads(cleaned)
        memo = _coerce_narrative_memo(parsed)
        assert memo is not None
        assert memo["label"] == "HOLD"
        assert memo["confidence"] == 80

    def test_scratchpad_wrapped_json(self):
        import json
        raw = (
            '<scratchpad>internal reasoning</scratchpad>\n'
            '{"label": "EXIT", "summary": "Close position.", "confidence": 90}'
        )
        cleaned = sanitize_model_text(raw)
        parsed = json.loads(cleaned)
        memo = _coerce_narrative_memo(parsed)
        assert memo is not None
        assert memo["label"] == "CLOSE"  # EXIT → CLOSE via legacy mapping

    def test_malformed_triggers_fallback(self):
        raw = "<think>lots of thinking</think>not valid json at all"
        cleaned = sanitize_model_text(raw)
        try:
            import json
            json.loads(cleaned)
            parsed = True
        except Exception:
            parsed = False
        assert parsed is False  # Can't parse → fallback should be used
        fallback = _build_fallback_narrative("WINNING", 70)
        assert fallback["label"] == "HOLD"
        assert fallback["confidence"] == 0


# ═══════════════════════════════════════════════════════════════
# _sanitize_model_analysis — unified recommendation schema
# ═══════════════════════════════════════════════════════════════

class TestSanitizeModelAnalysis:
    """Verify _sanitize_model_analysis uses the unified HOLD/REDUCE/CLOSE/URGENT_REVIEW enum."""

    def _base_raw(self, **overrides):
        raw = {
            "headline": "Test headline",
            "recommendation": "HOLD",
            "confidence": 75,
            "thesis_status": "INTACT",
            "summary": "Position looks fine.",
            "key_risks": ["Risk A"],
            "key_supports": ["Support A"],
            "technical_state": {},
            "action_plan": {"urgency": "LOW"},
            "memo": {},
        }
        raw.update(overrides)
        return raw

    def test_outputs_recommendation_field(self):
        """Output dict should use 'recommendation' not 'stance'."""
        result = _sanitize_model_analysis(self._base_raw())
        assert "recommendation" in result
        assert "stance" not in result
        assert result["recommendation"] == "HOLD"

    def test_valid_recommendations_pass_through(self):
        for rec in ("HOLD", "REDUCE", "CLOSE", "URGENT_REVIEW"):
            result = _sanitize_model_analysis(self._base_raw(recommendation=rec))
            assert result["recommendation"] == rec

    def test_legacy_exit_maps_to_close(self):
        result = _sanitize_model_analysis(self._base_raw(recommendation="EXIT"))
        assert result["recommendation"] == "CLOSE"

    def test_legacy_add_maps_to_hold(self):
        result = _sanitize_model_analysis(self._base_raw(recommendation="ADD"))
        assert result["recommendation"] == "HOLD"

    def test_legacy_watch_maps_to_hold(self):
        result = _sanitize_model_analysis(self._base_raw(recommendation="WATCH"))
        assert result["recommendation"] == "HOLD"

    def test_legacy_stance_field_fallback(self):
        """If response uses old 'stance' field instead of 'recommendation', still works."""
        raw = self._base_raw()
        del raw["recommendation"]
        raw["stance"] = "REDUCE"
        result = _sanitize_model_analysis(raw)
        assert result["recommendation"] == "REDUCE"

    def test_legacy_stance_exit_maps_to_close(self):
        raw = self._base_raw()
        del raw["recommendation"]
        raw["stance"] = "EXIT"
        result = _sanitize_model_analysis(raw)
        assert result["recommendation"] == "CLOSE"

    def test_invalid_recommendation_defaults_hold(self):
        result = _sanitize_model_analysis(self._base_raw(recommendation="YOLO"))
        assert result["recommendation"] == "HOLD"

    def test_missing_recommendation_defaults_hold(self):
        raw = self._base_raw()
        del raw["recommendation"]
        result = _sanitize_model_analysis(raw)
        assert result["recommendation"] == "HOLD"


class TestBuildFallbackAnalysis:
    """Verify _build_fallback_analysis uses the unified enum."""

    def test_fallback_uses_hold(self):
        result = _build_fallback_analysis("Some error reason")
        assert result["recommendation"] == "HOLD"
        assert "stance" not in result

    def test_fallback_has_required_fields(self):
        result = _build_fallback_analysis("timeout")
        assert result["confidence"] == 0
        assert result["thesis_status"] == "INTACT"
        assert isinstance(result["key_risks"], list)


# ═══════════════════════════════════════════════════════════════
# _coerce_narrative_memo — legacy mapping
# ═══════════════════════════════════════════════════════════════

class TestNarrativeMemoLegacyMapping:
    """Verify _coerce_narrative_memo maps legacy enum values correctly."""

    def test_exit_maps_to_close(self):
        result = _coerce_narrative_memo({"label": "EXIT", "summary": "Close out"})
        assert result["label"] == "CLOSE"

    def test_add_maps_to_hold(self):
        result = _coerce_narrative_memo({"label": "ADD", "summary": "Add more"})
        assert result["label"] == "HOLD"

    def test_watch_maps_to_hold(self):
        result = _coerce_narrative_memo({"label": "WATCH", "summary": "Keep watching"})
        assert result["label"] == "HOLD"

    def test_close_passes_through(self):
        result = _coerce_narrative_memo({"label": "CLOSE", "summary": "Close it"})
        assert result["label"] == "CLOSE"

    def test_urgent_review_passes_through(self):
        result = _coerce_narrative_memo({"label": "URGENT_REVIEW", "summary": "Review now"})
        assert result["label"] == "URGENT_REVIEW"
