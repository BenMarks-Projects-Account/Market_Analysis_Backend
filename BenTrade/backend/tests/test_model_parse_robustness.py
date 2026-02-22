"""Tests for the robust LLM JSON extraction and model_evaluation coercion in common/utils.py.

Covers:
  - Code fence stripping
  - Bare JSON object extraction
  - Various LLM response shapes (bare eval, list-of-trades, nested dict)
  - Tolerant field normalization
  - Failure cases (truly unparseable, missing keys)
"""
import json

from common.utils import (
    _find_json_block,
    _coerce_model_evaluation,
    _normalize_eval,
    _strip_code_fences,
    _looks_like_eval,
    _try_legacy_list_shape,
)


# ---------------------------------------------------------------------------
# _strip_code_fences
# ---------------------------------------------------------------------------

class TestStripCodeFences:
    def test_removes_json_fence(self):
        text = '```json\n{"recommendation": "ACCEPT"}\n```'
        assert _strip_code_fences(text) == '{"recommendation": "ACCEPT"}'

    def test_removes_plain_fence(self):
        text = '```\n{"key": 1}\n```'
        assert _strip_code_fences(text) == '{"key": 1}'

    def test_no_fence_passthrough(self):
        text = '{"recommendation": "REJECT"}'
        assert _strip_code_fences(text) == text

    def test_surrounding_whitespace(self):
        text = '  ```json\n  {"a": 1}  \n```  '
        result = _strip_code_fences(text)
        assert '"a"' in result


# ---------------------------------------------------------------------------
# _find_json_block
# ---------------------------------------------------------------------------

class TestFindJsonBlock:
    def test_plain_json_object(self):
        raw = '{"recommendation": "ACCEPT", "confidence": 0.8}'
        parsed = _find_json_block(raw)
        assert parsed is not None
        assert parsed["recommendation"] == "ACCEPT"

    def test_json_in_code_fence(self):
        raw = '```json\n{"recommendation": "REJECT", "confidence": 0.9}\n```'
        parsed = _find_json_block(raw)
        assert parsed is not None
        assert parsed["recommendation"] == "REJECT"

    def test_json_array(self):
        raw = '[{"recommendation": "NEUTRAL"}]'
        parsed = _find_json_block(raw)
        assert isinstance(parsed, list)
        assert parsed[0]["recommendation"] == "NEUTRAL"

    def test_leading_chatter(self):
        raw = 'Here is my analysis:\n\n{"recommendation": "ACCEPT", "confidence": 0.7}'
        parsed = _find_json_block(raw)
        assert parsed is not None
        assert parsed["recommendation"] == "ACCEPT"

    def test_trailing_chatter(self):
        raw = '{"recommendation": "ACCEPT", "confidence": 0.7}\n\nI hope this helps!'
        parsed = _find_json_block(raw)
        assert parsed is not None
        assert parsed["recommendation"] == "ACCEPT"

    def test_completely_invalid(self):
        raw = 'No JSON here at all, just plain text.'
        assert _find_json_block(raw) is None

    def test_empty_string(self):
        assert _find_json_block('') is None

    def test_fence_with_chatter(self):
        raw = 'Sure, here is the evaluation:\n```json\n{"recommendation":"ACCEPT","confidence":0.85,"risk_level":"Low","key_factors":["good EV"],"summary":"looks good"}\n```\nLet me know if you need more!'
        parsed = _find_json_block(raw)
        assert parsed is not None
        assert parsed["recommendation"] == "ACCEPT"
        assert parsed["confidence"] == 0.85


# ---------------------------------------------------------------------------
# _looks_like_eval
# ---------------------------------------------------------------------------

class TestLooksLikeEval:
    def test_full_eval(self):
        d = {"recommendation": "ACCEPT", "confidence": 0.8, "risk_level": "Low", "key_factors": [], "summary": "ok"}
        assert _looks_like_eval(d)

    def test_partial_eval(self):
        d = {"recommendation": "REJECT", "summary": "bad trade"}
        assert _looks_like_eval(d)

    def test_unrelated_dict(self):
        d = {"symbol": "SPY", "strike": 500}
        assert not _looks_like_eval(d)

    def test_empty_dict(self):
        assert not _looks_like_eval({})

    def test_not_dict(self):
        assert not _looks_like_eval("string")


# ---------------------------------------------------------------------------
# _normalize_eval
# ---------------------------------------------------------------------------

class TestNormalizeEval:
    def test_valid_accept(self):
        raw = {"recommendation": "ACCEPT", "confidence": 0.85, "risk_level": "Low",
               "key_factors": ["good EV", "high POP"], "summary": "Looks good"}
        result = _normalize_eval(raw)
        assert result["recommendation"] == "ACCEPT"
        assert result["confidence"] == 0.85
        assert result["risk_level"] == "Low"
        assert len(result["key_factors"]) == 2

    def test_unknown_recommendation_defaults_neutral(self):
        raw = {"recommendation": "MAYBE", "confidence": 0.5}
        result = _normalize_eval(raw)
        assert result["recommendation"] == "NEUTRAL"

    def test_missing_confidence_defaults(self):
        raw = {"recommendation": "REJECT"}
        result = _normalize_eval(raw)
        assert result["confidence"] == 0.5

    def test_confidence_clamped(self):
        raw = {"recommendation": "ACCEPT", "confidence": 5.0}
        result = _normalize_eval(raw)
        assert result["confidence"] == 1.0

    def test_key_factors_from_string(self):
        raw = {"recommendation": "NEUTRAL", "key_factors": "single factor"}
        result = _normalize_eval(raw)
        assert result["key_factors"] == ["single factor"]

    def test_key_factors_truncated(self):
        raw = {"recommendation": "NEUTRAL", "key_factors": list(range(10))}
        result = _normalize_eval(raw)
        assert len(result["key_factors"]) <= 6


# ---------------------------------------------------------------------------
# _coerce_model_evaluation
# ---------------------------------------------------------------------------

class TestCoerceModelEvaluation:
    """The critical function — extracts model_evaluation from many LLM output shapes."""

    def test_bare_eval_object(self):
        """Model returned just the evaluation dict (most common mismatch)."""
        parsed = {"recommendation": "ACCEPT", "confidence": 0.8, "risk_level": "Low",
                  "key_factors": ["positive EV"], "summary": "Good trade"}
        result = _coerce_model_evaluation(parsed)
        assert result is not None
        assert result["recommendation"] == "ACCEPT"

    def test_list_of_one_trade_with_model_evaluation(self):
        """Old expected shape: array of 1 trade with model_evaluation key."""
        trade = {"symbol": "SPY", "model_evaluation": {
            "recommendation": "REJECT", "confidence": 0.9, "risk_level": "High",
            "key_factors": ["neg EV"], "summary": "Bad"
        }}
        result = _coerce_model_evaluation([trade])
        assert result is not None
        assert result["recommendation"] == "REJECT"

    def test_dict_with_model_evaluation_key(self):
        """Model wrapped response in {model_evaluation: {...}}."""
        parsed = {"model_evaluation": {
            "recommendation": "NEUTRAL", "confidence": 0.5, "risk_level": "Moderate",
            "key_factors": [], "summary": "Unclear"
        }}
        result = _coerce_model_evaluation(parsed)
        assert result is not None
        assert result["recommendation"] == "NEUTRAL"

    def test_dict_with_trades_key(self):
        """Model returned {trades: [{...model_evaluation...}]}."""
        parsed = {"trades": [{"symbol": "SPY", "model_evaluation": {
            "recommendation": "ACCEPT", "confidence": 0.7, "risk_level": "Low",
            "key_factors": [], "summary": "OK"
        }}]}
        result = _coerce_model_evaluation(parsed)
        assert result is not None
        assert result["recommendation"] == "ACCEPT"

    def test_list_with_bare_eval_as_element(self):
        """Model returned [evaluation_obj] (bare eval in a list)."""
        parsed = [{"recommendation": "ACCEPT", "confidence": 0.75, "risk_level": "Low",
                   "key_factors": ["ok"], "summary": "Fine"}]
        result = _coerce_model_evaluation(parsed)
        assert result is not None
        assert result["recommendation"] == "ACCEPT"

    def test_none_input(self):
        assert _coerce_model_evaluation(None) is None

    def test_unrecognized_shape(self):
        assert _coerce_model_evaluation(42) is None

    def test_empty_dict(self):
        assert _coerce_model_evaluation({}) is None


# ---------------------------------------------------------------------------
# _try_legacy_list_shape
# ---------------------------------------------------------------------------

class TestTryLegacyListShape:
    def test_list_of_one_trade(self):
        parsed = [{"symbol": "SPY", "model_evaluation": {
            "recommendation": "ACCEPT", "confidence": 0.8, "key_factors": [], "summary": "ok"
        }}]
        result = _try_legacy_list_shape(parsed, "[TEST]")
        assert result is not None
        assert result["recommendation"] == "ACCEPT"

    def test_dict_with_trades(self):
        parsed = {"trades": [{"model_evaluation": {
            "recommendation": "REJECT", "confidence": 0.9, "key_factors": [], "summary": "no"
        }}]}
        result = _try_legacy_list_shape(parsed, "[TEST]")
        assert result is not None
        assert result["recommendation"] == "REJECT"

    def test_non_list_returns_none(self):
        assert _try_legacy_list_shape("not a list", "[TEST]") is None

    def test_empty_list_returns_none(self):
        assert _try_legacy_list_shape([], "[TEST]") is None


# ---------------------------------------------------------------------------
# End-to-end: simulate what the LLM might actually return
# ---------------------------------------------------------------------------

class TestEndToEndLLMOutputs:
    """Simulate realistic LLM outputs and verify they parse correctly."""

    def test_clean_json_object(self):
        """Model perfectly follows instructions."""
        raw = '{"recommendation":"ACCEPT","confidence":0.82,"risk_level":"Low","key_factors":["Positive EV","High POP"],"summary":"This trade has favorable risk-adjusted returns."}'
        parsed = _find_json_block(raw)
        result = _coerce_model_evaluation(parsed)
        assert result is not None
        assert result["recommendation"] == "ACCEPT"
        assert result["confidence"] == 0.82

    def test_code_fenced_with_chatter(self):
        """Model wraps in code fence with leading/trailing commentary."""
        raw = (
            "Based on my analysis of this credit spread:\n\n"
            "```json\n"
            '{"recommendation": "REJECT", "confidence": 0.88, "risk_level": "High", '
            '"key_factors": ["Negative EV", "Wide bid-ask"], "summary": "Risk too high."}\n'
            "```\n\n"
            "This trade should be avoided."
        )
        parsed = _find_json_block(raw)
        result = _coerce_model_evaluation(parsed)
        assert result is not None
        assert result["recommendation"] == "REJECT"
        assert result["confidence"] == 0.88

    def test_legacy_array_format(self):
        """Model returns the old expected format: array with full trade + model_evaluation."""
        trade_with_eval = {
            "symbol": "SPY", "spread_type": "put_credit",
            "model_evaluation": {
                "recommendation": "NEUTRAL", "confidence": 0.45,
                "risk_level": "Moderate", "key_factors": ["Unclear regime"],
                "summary": "Mixed signals."
            }
        }
        raw = json.dumps([trade_with_eval])
        parsed = _find_json_block(raw)
        result = _coerce_model_evaluation(parsed)
        assert result is not None
        assert result["recommendation"] == "NEUTRAL"

    def test_multiline_pretty_printed(self):
        """Model returns pretty-printed JSON."""
        raw = """{
    "recommendation": "ACCEPT",
    "confidence": 0.76,
    "risk_level": "Low",
    "key_factors": [
        "Positive expected value",
        "Favorable Greeks",
        "High probability of profit"
    ],
    "summary": "This put credit spread on SPY has a strong risk-reward profile."
}"""
        parsed = _find_json_block(raw)
        result = _coerce_model_evaluation(parsed)
        assert result is not None
        assert result["recommendation"] == "ACCEPT"
        assert len(result["key_factors"]) == 3
