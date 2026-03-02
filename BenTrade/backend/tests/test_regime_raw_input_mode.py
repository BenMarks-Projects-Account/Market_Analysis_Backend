"""Tests for raw-only regime model analysis payload and output format.

Validates that:
  1. _extract_regime_raw_inputs extracts ONLY raw fields, not derived scores/labels.
  2. analyze_regime sends raw-only payload to the model (no derived fields leak).
  3. _coerce_regime_model_output accepts the new raw_inputs_used section.
  4. The trace metadata is attached to the response.
  5. Output format parity: all original keys still present.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from common.model_analysis import (
    _coerce_regime_model_output,
    _extract_regime_raw_inputs,
    _REGIME_DERIVED_FIELDS,
    analyze_regime,
    compute_regime_deltas,
    extract_engine_regime_summary,
)


# ─── Fixtures ────────────────────────────────────────────────────────

def _make_regime_data() -> dict[str, Any]:
    """Return a realistic regime payload as produced by RegimeService._compute()."""
    return {
        "as_of": "2026-03-02T15:00:00+00:00",
        "regime_label": "RISK_ON",
        "regime_score": 72.5,
        "components": {
            "trend": {
                "score": 80.0,
                "raw_points": 20.0,
                "signals": ["Close 590.12 > EMA20 585.30", "SMA50 580.0 > SMA200 560.0"],
                "inputs": {
                    "close": 590.12,
                    "ema20": 585.30,
                    "ema50": 578.10,
                    "sma50": 580.0,
                    "sma200": 560.0,
                    "close_gt_ema20": True,
                    "close_gt_ema50": True,
                    "sma50_gt_sma200": True,
                },
            },
            "volatility": {
                "score": 100.0,
                "signals": ["VIX < 18 (+25)"],
                "inputs": {
                    "vix": 14.5,
                    "vix_5d_change": -0.03,
                },
            },
            "breadth": {
                "score": 72.0,
                "signals": ["8/11 sectors above EMA20"],
                "inputs": {
                    "sectors_above_ema20": 8,
                    "sectors_total": 11,
                    "pct_above_ema20": 0.7272,
                },
            },
            "rates": {
                "score": 66.7,
                "signals": ["10Y now 4.25%"],
                "inputs": {
                    "ten_year_yield": 4.25,
                    "ten_year_5d_change_bps": 3.0,
                },
            },
            "momentum": {
                "score": 90.0,
                "signals": ["RSI in ideal band 45-65 (+10)"],
                "inputs": {
                    "rsi14": 55.3,
                },
            },
        },
        "suggested_playbook": {
            "primary": ["put_credit_spread", "covered_call"],
            "avoid": ["short_gamma"],
            "notes": ["Favor bullish premium-selling structures"],
        },
        "source_health": {
            "tradier": {"status": "ok"},
            "fred": {"status": "ok"},
        },
    }


def _make_model_response() -> dict[str, Any]:
    """A valid model response with the new raw_inputs_used key."""
    return {
        "executive_summary": "Markets are in a constructive risk-on posture. SPY trades above all key moving averages.",
        "regime_breakdown": {
            "trend": "SPY at 590.12 well above EMA20 (585.30) and SMA200 (560.0), confirming uptrend.",
            "volatility": "VIX at 14.5 is firmly in low-vol territory, supportive of premium-selling.",
            "breadth": "8 of 11 sector ETFs above EMA20 indicates broad market participation.",
            "rates": "10Y yield at 4.25% with minimal 5-day change suggests rate stability.",
            "momentum": "RSI14 at 55.3 is in the ideal neutral-bullish zone.",
        },
        "primary_fit": "Low VIX and stable trend support put credit spreads and covered calls.",
        "avoid_rationale": "Short gamma is risky despite low VIX because of complacency risk.",
        "change_triggers": [
            "VIX rises above 20",
            "SPY breaks below SMA50 at 580",
            "10Y yield spikes >20bps in 5 days",
        ],
        "confidence_caveats": "High confidence (0.85). All raw inputs present.",
        "confidence": 0.85,
        "raw_inputs_used": {
            "spy_price": 590.12,
            "vix_spot": 14.5,
            "rsi14": 55.3,
            "missing": [],
        },
    }


# ─── Tests: _extract_regime_raw_inputs ───────────────────────────────

class TestExtractRegimeRawInputs:
    def test_extracts_all_raw_fields(self) -> None:
        regime = _make_regime_data()
        raw = _extract_regime_raw_inputs(regime)

        assert raw["spy_price"] == 590.12
        assert raw["spy_ema20"] == 585.30
        assert raw["spy_ema50"] == 578.10
        assert raw["spy_sma50"] == 580.0
        assert raw["spy_sma200"] == 560.0
        assert raw["vix_spot"] == 14.5
        assert raw["vix_5d_change_pct"] == -0.03
        assert raw["sectors_above_ema20"] == 8
        assert raw["sectors_total"] == 11
        assert raw["pct_sectors_above_ema20"] == pytest.approx(0.7272)
        assert raw["ten_year_yield"] == 4.25
        assert raw["ten_year_5d_change_bps"] == 3.0
        assert raw["rsi14"] == 55.3

    def test_excludes_derived_fields(self) -> None:
        """No derived labels, scores, booleans, or playbook data in raw output."""
        regime = _make_regime_data()
        raw = _extract_regime_raw_inputs(regime)
        raw_str = json.dumps(raw)

        # Must not contain any derived field names
        for forbidden in ("regime_label", "regime_score", "suggested_playbook",
                          "score", "raw_points", "signals",
                          "close_gt_ema20", "close_gt_ema50", "sma50_gt_sma200",
                          "RISK_ON", "RISK_OFF", "NEUTRAL"):
            assert forbidden not in raw_str, f"Derived field '{forbidden}' leaked into raw inputs"

    def test_handles_missing_components(self) -> None:
        """If regime data has no components, all raw fields are None."""
        raw = _extract_regime_raw_inputs({})
        assert raw["spy_price"] is None
        assert raw["vix_spot"] is None
        assert raw["rsi14"] is None
        # Should still have all expected keys
        assert len(raw) == 13

    def test_handles_partial_components(self) -> None:
        """Only trend component provided — others are None."""
        regime = {
            "components": {
                "trend": {
                    "inputs": {"close": 500.0, "ema20": 498.0},
                },
            },
        }
        raw = _extract_regime_raw_inputs(regime)
        assert raw["spy_price"] == 500.0
        assert raw["spy_ema20"] == 498.0
        assert raw["vix_spot"] is None
        assert raw["rsi14"] is None


# ─── Tests: _coerce_regime_model_output ──────────────────────────────

class TestCoerceRegimeModelOutput:
    def test_accepts_new_raw_inputs_used_key(self) -> None:
        response = _make_model_response()
        result = _coerce_regime_model_output(response)
        assert result is not None
        assert "raw_inputs_used" in result
        assert isinstance(result["raw_inputs_used"], dict)

    def test_format_parity_with_original_keys(self) -> None:
        """All original output keys are still present."""
        response = _make_model_response()
        result = _coerce_regime_model_output(response)
        assert result is not None
        for key in ("executive_summary", "regime_breakdown", "primary_fit",
                     "avoid_rationale", "change_triggers", "confidence_caveats",
                     "confidence"):
            assert key in result, f"Missing expected key: {key}"

    def test_handles_missing_raw_inputs_used(self) -> None:
        """If model doesn't return raw_inputs_used, it's None (graceful)."""
        response = _make_model_response()
        del response["raw_inputs_used"]
        result = _coerce_regime_model_output(response)
        assert result is not None
        assert result["raw_inputs_used"] is None


# ─── Tests: analyze_regime (mocked LLM call) ────────────────────────

class TestAnalyzeRegimeRawOnly:
    @patch("common.model_analysis._requests", create=True)
    def test_payload_contains_only_raw_inputs(self, _mock_requests) -> None:
        """The user message sent to the LLM must contain regime_raw_inputs and
        metadata, and must NOT contain regime_label, regime_score, or
        suggested_playbook."""
        regime = _make_regime_data()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps(_make_model_response()),
                },
            }],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            result = analyze_regime(regime_data=regime)

        # Extract the user message from the call
        call_args = mock_post.call_args
        assert call_args is not None
        sent_payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        messages = sent_payload["messages"]
        user_content = messages[1]["content"]

        user_data = json.loads(user_content)

        # Must have regime_raw_inputs and metadata
        assert "regime_raw_inputs" in user_data
        assert "metadata" in user_data

        # Must NOT have derived fields
        assert "regime" not in user_data, "Derived 'regime' block should not be in payload"
        assert "suggested_playbook" not in user_data, "suggested_playbook should not be in payload"
        assert "enriched_playbook" not in user_data, "enriched_playbook should not be in payload"
        assert "market_values" not in user_data, "market_values should not be in payload"

        # Verify raw inputs don't contain derived fields
        raw_str = json.dumps(user_data["regime_raw_inputs"])
        for forbidden in ("regime_label", "regime_score", "close_gt_ema20"):
            assert forbidden not in raw_str

    @patch("common.model_analysis._requests", create=True)
    def test_trace_metadata_attached_to_response(self, _mock_requests) -> None:
        regime = _make_regime_data()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps(_make_model_response()),
                },
            }],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            result = analyze_regime(regime_data=regime)

        assert "_trace" in result
        trace = result["_trace"]
        assert trace["model_regime_input_mode"] == "raw_only"
        assert trace["included_fields_count"] > 0
        assert trace["excluded_fields_count"] == len(_REGIME_DERIVED_FIELDS)
        assert isinstance(trace["excluded_derived_field_names"], list)
        assert isinstance(trace["missing_raw_fields"], list)
        assert isinstance(trace["regime_raw_inputs_snapshot"], dict)

    @patch("common.model_analysis._requests", create=True)
    def test_prompt_instructs_no_precomputed_labels(self, _mock_requests) -> None:
        """System prompt must instruct model not to use precomputed labels."""
        regime = _make_regime_data()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps(_make_model_response()),
                },
            }],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            analyze_regime(regime_data=regime)

        sent_payload = mock_post.call_args[1]["json"]
        system_prompt = sent_payload["messages"][0]["content"]

        assert "Do NOT use any precomputed regime labels" in system_prompt
        assert "regime_raw_inputs" in system_prompt
        assert "raw_inputs_used" in system_prompt


# ─── Tests: _REGIME_DERIVED_FIELDS constant ──────────────────────────

class TestDerivedFieldsConstant:
    def test_contains_expected_exclusions(self) -> None:
        assert "regime_label" in _REGIME_DERIVED_FIELDS
        assert "regime_score" in _REGIME_DERIVED_FIELDS
        assert "suggested_playbook" in _REGIME_DERIVED_FIELDS

    def test_not_empty(self) -> None:
        assert len(_REGIME_DERIVED_FIELDS) >= 5


# ─── Tests: extract_engine_regime_summary ────────────────────────────

class TestExtractEngineRegimeSummary:
    def test_risk_on_maps_correctly(self) -> None:
        regime = _make_regime_data()
        summary = extract_engine_regime_summary(regime)
        assert summary["risk_regime_label"] == "Risk-On"

    def test_risk_off_maps_correctly(self) -> None:
        regime = _make_regime_data()
        regime["regime_label"] = "RISK_OFF"
        summary = extract_engine_regime_summary(regime)
        assert summary["risk_regime_label"] == "Risk-Off"

    def test_neutral_maps_correctly(self) -> None:
        regime = _make_regime_data()
        regime["regime_label"] = "NEUTRAL"
        summary = extract_engine_regime_summary(regime)
        assert summary["risk_regime_label"] == "Neutral"

    def test_unknown_label_defaults_neutral(self) -> None:
        regime = _make_regime_data()
        regime["regime_label"] = "SOME_UNKNOWN"
        summary = extract_engine_regime_summary(regime)
        assert summary["risk_regime_label"] == "Neutral"

    def test_trend_uptrend(self) -> None:
        """close > ema20 and sma50 > sma200 → 'Uptrend'"""
        regime = _make_regime_data()
        summary = extract_engine_regime_summary(regime)
        assert summary["trend_label"] == "Uptrend"

    def test_trend_downtrend(self) -> None:
        """close < sma200 → 'Downtrend'"""
        regime = _make_regime_data()
        regime["components"]["trend"]["inputs"]["close"] = 550.0  # below SMA200 560
        summary = extract_engine_regime_summary(regime)
        assert summary["trend_label"] == "Downtrend"

    def test_trend_sideways(self) -> None:
        """close < ema20 but > sma200 and sma50 < sma200 → 'Sideways'"""
        regime = _make_regime_data()
        regime["components"]["trend"]["inputs"]["close"] = 583.0  # below ema20=585.30
        regime["components"]["trend"]["inputs"]["sma50"] = 555.0  # below sma200=560
        summary = extract_engine_regime_summary(regime)
        assert summary["trend_label"] == "Sideways"

    def test_trend_unknown_when_missing(self) -> None:
        regime = {"components": {}}
        summary = extract_engine_regime_summary(regime)
        assert summary["trend_label"] == "Unknown"

    def test_vol_low(self) -> None:
        regime = _make_regime_data()  # vix=14.5
        summary = extract_engine_regime_summary(regime)
        assert summary["vol_regime_label"] == "Low"

    def test_vol_moderate(self) -> None:
        regime = _make_regime_data()
        regime["components"]["volatility"]["inputs"]["vix"] = 22.0
        summary = extract_engine_regime_summary(regime)
        assert summary["vol_regime_label"] == "Moderate"

    def test_vol_high(self) -> None:
        regime = _make_regime_data()
        regime["components"]["volatility"]["inputs"]["vix"] = 30.0
        summary = extract_engine_regime_summary(regime)
        assert summary["vol_regime_label"] == "High"

    def test_vol_unknown_when_missing(self) -> None:
        regime = {"components": {"volatility": {"inputs": {}}}}
        summary = extract_engine_regime_summary(regime)
        assert summary["vol_regime_label"] == "Unknown"

    def test_confidence_from_regime_score(self) -> None:
        regime = _make_regime_data()  # regime_score=72.5 → 0.725 → round(2) = 0.72
        summary = extract_engine_regime_summary(regime)
        assert summary["confidence"] == pytest.approx(0.72, abs=0.005)

    def test_confidence_none_when_missing(self) -> None:
        regime = {}
        summary = extract_engine_regime_summary(regime)
        assert summary["confidence"] is None

    def test_key_drivers_present(self) -> None:
        regime = _make_regime_data()
        summary = extract_engine_regime_summary(regime)
        assert isinstance(summary["key_drivers"], list)
        assert len(summary["key_drivers"]) > 0
        assert len(summary["key_drivers"]) <= 3

    def test_key_drivers_sorted_by_score(self) -> None:
        """Highest-scoring components appear first."""
        regime = _make_regime_data()
        summary = extract_engine_regime_summary(regime)
        # Volatility (100) > Momentum (90) > Trend (80)
        assert "Volatility:" in summary["key_drivers"][0]

    def test_empty_regime_data(self) -> None:
        summary = extract_engine_regime_summary({})
        assert summary["risk_regime_label"] == "Neutral"
        assert summary["trend_label"] == "Unknown"
        assert summary["vol_regime_label"] == "Unknown"
        assert summary["confidence"] is None
        assert summary["key_drivers"] == []

    def test_output_has_all_required_keys(self) -> None:
        regime = _make_regime_data()
        summary = extract_engine_regime_summary(regime)
        for key in ("risk_regime_label", "trend_label", "vol_regime_label",
                     "confidence", "key_drivers"):
            assert key in summary, f"Missing key: {key}"


# ─── Tests: compute_regime_deltas ────────────────────────────────────

class TestComputeRegimeDeltas:
    def test_full_agreement(self) -> None:
        engine = {
            "risk_regime_label": "Risk-On",
            "trend_label": "Uptrend",
            "vol_regime_label": "Low",
            "confidence": 0.75,
            "key_drivers": ["Volatility: VIX < 18"],
        }
        model = {
            "risk_regime_label": "Risk-On",
            "trend_label": "Uptrend",
            "vol_regime_label": "Low",
            "confidence": 0.80,
            "key_drivers": ["Low VIX supportive"],
        }
        result = compute_regime_deltas(engine, model)
        assert result["disagreement_count"] == 0
        for key in ("risk", "trend", "vol", "confidence"):
            assert result["deltas"][key]["match"] is True

    def test_all_disagree(self) -> None:
        engine = {
            "risk_regime_label": "Risk-On",
            "trend_label": "Uptrend",
            "vol_regime_label": "Low",
            "confidence": 0.80,
        }
        model = {
            "risk_regime_label": "Risk-Off",
            "trend_label": "Downtrend",
            "vol_regime_label": "High",
            "confidence": 0.30,
        }
        result = compute_regime_deltas(engine, model)
        assert result["disagreement_count"] == 4
        for key in ("risk", "trend", "vol", "confidence"):
            assert result["deltas"][key]["match"] is False

    def test_case_insensitive_match(self) -> None:
        engine = {"risk_regime_label": "Risk-On", "trend_label": "Uptrend",
                  "vol_regime_label": "Low", "confidence": 0.5}
        model = {"risk_regime_label": "risk-on", "trend_label": "UPTREND",
                 "vol_regime_label": "low", "confidence": 0.5}
        result = compute_regime_deltas(engine, model)
        assert result["disagreement_count"] == 0

    def test_confidence_tolerance(self) -> None:
        """Within ±0.10 counts as Match."""
        engine = {"risk_regime_label": "Neutral", "trend_label": "Sideways",
                  "vol_regime_label": "Moderate", "confidence": 0.70}
        model = {"risk_regime_label": "Neutral", "trend_label": "Sideways",
                 "vol_regime_label": "Moderate", "confidence": 0.61}
        result = compute_regime_deltas(engine, model)
        assert result["deltas"]["confidence"]["match"] is True
        assert result["disagreement_count"] == 0

    def test_confidence_beyond_tolerance(self) -> None:
        engine = {"risk_regime_label": "Neutral", "trend_label": "Sideways",
                  "vol_regime_label": "Moderate", "confidence": 0.70}
        model = {"risk_regime_label": "Neutral", "trend_label": "Sideways",
                 "vol_regime_label": "Moderate", "confidence": 0.50}
        result = compute_regime_deltas(engine, model)
        assert result["deltas"]["confidence"]["match"] is False
        assert result["disagreement_count"] == 1

    def test_none_values_count_as_disagreement(self) -> None:
        engine = {"risk_regime_label": "Risk-On", "trend_label": "Uptrend",
                  "vol_regime_label": "Low", "confidence": 0.75}
        model = {"risk_regime_label": None, "trend_label": None,
                 "vol_regime_label": None, "confidence": None}
        result = compute_regime_deltas(engine, model)
        assert result["disagreement_count"] == 4

    def test_delta_detail_on_mismatch(self) -> None:
        engine = {"risk_regime_label": "Risk-On", "trend_label": "Uptrend",
                  "vol_regime_label": "Low", "confidence": 0.75}
        model = {"risk_regime_label": "Risk-Off", "trend_label": "Uptrend",
                 "vol_regime_label": "Low", "confidence": 0.75}
        result = compute_regime_deltas(engine, model)
        risk = result["deltas"]["risk"]
        assert risk["match"] is False
        assert "Risk-On" in risk["detail"]
        assert "Risk-Off" in risk["detail"]

    def test_delta_detail_none_on_match(self) -> None:
        engine = {"risk_regime_label": "Risk-On", "trend_label": "Uptrend",
                  "vol_regime_label": "Low", "confidence": 0.75}
        model = {"risk_regime_label": "Risk-On", "trend_label": "Uptrend",
                 "vol_regime_label": "Low", "confidence": 0.80}
        result = compute_regime_deltas(engine, model)
        for key in ("risk", "trend", "vol", "confidence"):
            assert result["deltas"][key]["detail"] is None


# ─── Tests: _coerce_regime_model_output (label extraction) ───────────

class TestCoerceRegimeModelOutputLabels:
    def test_extracts_label_fields(self) -> None:
        response = _make_model_response()
        response["risk_regime_label"] = "Risk-On"
        response["trend_label"] = "Uptrend"
        response["vol_regime_label"] = "Low"
        response["key_drivers"] = ["Low VIX", "Strong trend", "Broad breadth"]
        result = _coerce_regime_model_output(response)
        assert result["risk_regime_label"] == "Risk-On"
        assert result["trend_label"] == "Uptrend"
        assert result["vol_regime_label"] == "Low"
        assert result["key_drivers"] == ["Low VIX", "Strong trend", "Broad breadth"]

    def test_missing_labels_are_none(self) -> None:
        response = _make_model_response()
        result = _coerce_regime_model_output(response)
        assert result["risk_regime_label"] is None
        assert result["trend_label"] is None
        assert result["vol_regime_label"] is None
        assert result["key_drivers"] is None

    def test_key_drivers_capped_at_five(self) -> None:
        response = _make_model_response()
        response["key_drivers"] = [f"driver_{i}" for i in range(10)]
        result = _coerce_regime_model_output(response)
        assert len(result["key_drivers"]) == 5

    def test_key_drivers_string_coerced_to_list(self) -> None:
        response = _make_model_response()
        response["key_drivers"] = "Low VIX is the primary driver"
        result = _coerce_regime_model_output(response)
        assert result["key_drivers"] == ["Low VIX is the primary driver"]
