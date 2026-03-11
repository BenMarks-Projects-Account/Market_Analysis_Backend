"""Tests for engine_output_contract v1.1 — second-pass enhancements.

Coverage targets (beyond existing test_engine_output_contract.py):
─── engine_status / status_detail on success payloads
─── Degraded payload via build_degraded_output
─── Error payload via build_error_output
─── Malformed raw engine output entering the normalizer
─── Legacy cached payload detection and normalization
─── Mixed cache-era payload handling (downstream-safe)
─── Unified contract shape assertions (REQUIRED_FIELDS)
─── validate_normalized_output behavior
─── Context assembler fallback → contract unification
"""

import pytest
from datetime import datetime, timezone, timedelta

from app.services.engine_output_contract import (
    REQUIRED_FIELDS,
    VALID_ENGINE_STATUSES,
    ENGINE_METADATA,
    normalize_engine_output,
    build_error_output,
    build_degraded_output,
    detect_legacy_payload,
    normalize_legacy_payload,
    validate_normalized_output,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _good_breadth_payload():
    """Fully populated breadth service response."""
    return {
        "engine_result": {
            "engine": "breadth_participation",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "score": 72.5,
            "label": "Healthy Breadth",
            "short_label": "Healthy",
            "confidence_score": 85.0,
            "signal_quality": "high",
            "universe": {"name": "S&P 500", "expected_count": 503,
                          "actual_count": 498, "coverage_pct": 99.0},
            "pillar_scores": {"participation_breadth": 78.0, "trend_breadth": 65.0},
            "pillar_weights": {"participation_breadth": 0.30, "trend_breadth": 0.20},
            "pillar_explanations": {"participation_breadth": "Broad.", "trend_breadth": "OK."},
            "diagnostics": {"pillar_details": {}},
            "summary": "Breadth is healthy.",
            "trader_takeaway": "Good for directional.",
            "positive_contributors": ["Strong A/D"],
            "negative_contributors": ["Volume lagging"],
            "conflicting_signals": [],
            "warnings": [],
            "missing_inputs": [],
        },
        "data_quality": {
            "signal_quality": "high",
            "confidence_score": 85.0,
            "missing_inputs_count": 0,
            "warning_count": 0,
        },
        "compute_duration_s": 0.45,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _degraded_vol_payload():
    """Volatility payload with missing inputs and warnings."""
    return {
        "engine_result": {
            "engine": "volatility_options",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "score": 55.0,
            "label": "Neutral",
            "short_label": "Neutral",
            "confidence_score": 40.0,
            "signal_quality": "low",
            "pillar_scores": {"volatility_regime": 55.0},
            "pillar_weights": {"volatility_regime": 0.25},
            "pillar_explanations": {"volatility_regime": "Uncertain."},
            "diagnostics": {"pillar_details": {}},
            "summary": "Mixed signals.",
            "trader_takeaway": "Reduce size.",
            "positive_contributors": [],
            "negative_contributors": ["Skew elevated"],
            "conflicting_signals": ["VIX vs realized"],
            "warnings": ["VIX source delayed", "Skew data stale",
                          "Structure incomplete"],
            "missing_inputs": ["vix_futures_curve", "realized_vol_10d"],
        },
        "data_quality": {
            "signal_quality": "low",
            "confidence_score": 40.0,
            "missing_inputs_count": 2,
            "warning_count": 3,
        },
        "compute_duration_s": 0.3,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _empty_engine_payload():
    """Payload with engine_result: {} — engine produced nothing."""
    return {
        "engine_result": {},
        "data_quality": {},
        "compute_duration_s": 0,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _none_engine_payload():
    """Payload with engine_result: None — engine crashed."""
    return {
        "engine_result": None,
        "data_quality": {},
        "compute_duration_s": 0,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _legacy_cached_payload(*, score=60.0, label="Legacy Label"):
    """Legacy payload with no normalized key — simulates old cache entry."""
    return {
        "engine_result": {
            "score": score,
            "label": label,
            "short_label": "Legacy",
            "confidence_score": 50.0,
            "signal_quality": "medium",
            "summary": "From legacy cache.",
            "trader_takeaway": "Proceed with caution.",
            "warnings": ["stale data"],
            "missing_inputs": [],
        },
        "data_quality": {
            "signal_quality": "medium",
            "confidence_score": 50.0,
            "missing_inputs_count": 0,
            "warning_count": 1,
        },
        "compute_duration_s": 0.2,
        "as_of": "2026-01-01T12:00:00Z",
    }


def _pre_v11_normalized_payload():
    """Payload with a valid v1.0 normalized dict but no engine_status/status_detail."""
    return {
        "normalized": {
            "engine_key": "breadth_participation",
            "engine_name": "Breadth & Participation",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "score": 70.0,
            "label": "Healthy",
            "short_label": "Healthy",
            "confidence": 80.0,
            "signal_quality": "high",
            "time_horizon": "short_term",
            "freshness": {"compute_duration_s": 0.5, "cache_hit": None, "sources": None},
            "summary": "Good breadth.",
            "trader_takeaway": "Directional strategies.",
            "bull_factors": ["Strong A/D"],
            "bear_factors": [],
            "risks": [],
            "regime_tags": ["healthy"],
            "supporting_metrics": [],
            "contradiction_flags": [],
            "data_quality": {"confidence_score": 80, "signal_quality": "high",
                              "missing_inputs_count": 0, "warning_count": 0,
                              "coverage_pct": None},
            "warnings": [],
            "source_status": {"errors": {}, "proxy_count": 0, "direct_count": 0},
            "pillar_scores": [],
            "detail_sections": {},
            # Note: no engine_status, no status_detail (v1.0 shape)
        },
        "engine_result": {"score": 70.0},
        "data_quality": {},
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _news_failed_payload():
    """News payload where the engine returned nothing."""
    return {
        "internal_engine": None,
        "items": [],
        "macro_context": {},
        "source_freshness": [],
        "as_of": datetime.now(timezone.utc).isoformat(),
        "item_count": 0,
    }


# =====================================================================
#  1. Fully-normalized success payload
# =====================================================================

class TestSuccessPayload:

    def test_success_has_all_required_fields(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        for field in REQUIRED_FIELDS:
            assert field in r, f"missing: {field}"

    def test_success_engine_status_ok(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        assert r["engine_status"] == "ok"

    def test_success_status_detail_shape(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        sd = r["status_detail"]
        assert sd["normalization_source"] == "engine"
        assert sd["is_fallback"] is False
        assert sd["is_legacy"] is False
        assert sd["degraded_reasons"] == []

    def test_success_validates(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        ok, errors = validate_normalized_output(r)
        assert ok, f"Errors: {errors}"

    def test_all_engines_produce_engine_status(self):
        """Every engine produces engine_status + status_detail."""
        fixtures = {
            "breadth_participation": _good_breadth_payload(),
            "volatility_options": _degraded_vol_payload(),
        }
        for key, payload in fixtures.items():
            r = normalize_engine_output(key, payload)
            assert "engine_status" in r
            assert r["engine_status"] in VALID_ENGINE_STATUSES
            assert "status_detail" in r
            assert isinstance(r["status_detail"], dict)

    def test_news_success_has_engine_status(self):
        payload = {
            "internal_engine": {
                "score": 58.0,
                "regime_label": "Mixed",
                "components": {"headline": {"score": 60, "signal": "ok"}},
                "weights": {"headline": 0.30},
                "explanation": {"summary": "ok", "signal_quality": "medium",
                                "trader_takeaway": "wait"},
                "as_of": datetime.now(timezone.utc).isoformat(),
            },
            "items": [{"headline": "test"}],
            "macro_context": {},
            "source_freshness": [{"source": "finnhub", "status": "ok",
                                   "error": None}],
            "as_of": datetime.now(timezone.utc).isoformat(),
            "item_count": 1,
        }
        r = normalize_engine_output("news_sentiment", payload)
        assert r["engine_status"] in VALID_ENGINE_STATUSES
        ok, errs = validate_normalized_output(r)
        assert ok, f"Errors: {errs}"


# =====================================================================
#  2. Degraded payload with partial data
# =====================================================================

class TestDegradedPayload:

    def test_degraded_from_low_quality(self):
        r = normalize_engine_output("volatility_options", _degraded_vol_payload())
        assert r["engine_status"] == "degraded"
        assert len(r["status_detail"]["degraded_reasons"]) > 0

    def test_degraded_reasons_include_missing(self):
        r = normalize_engine_output("volatility_options", _degraded_vol_payload())
        reasons = r["status_detail"]["degraded_reasons"]
        assert any("missing_inputs" in reason for reason in reasons)

    def test_degraded_reasons_include_warnings(self):
        r = normalize_engine_output("volatility_options", _degraded_vol_payload())
        reasons = r["status_detail"]["degraded_reasons"]
        assert any("warnings" in reason or "elevated_warnings" in reason
                    for reason in reasons)

    def test_degraded_still_has_score(self):
        r = normalize_engine_output("volatility_options", _degraded_vol_payload())
        assert r["score"] == 55.0

    def test_degraded_validates(self):
        r = normalize_engine_output("volatility_options", _degraded_vol_payload())
        ok, errors = validate_normalized_output(r)
        assert ok, f"Errors: {errors}"

    def test_build_degraded_output(self):
        r = build_degraded_output(
            "breadth_participation",
            _good_breadth_payload(),
            reasons=["upstream_source_stale"],
        )
        assert r["engine_status"] == "degraded"
        assert "upstream_source_stale" in r["status_detail"]["degraded_reasons"]

    def test_build_degraded_preserves_score(self):
        r = build_degraded_output("breadth_participation", _good_breadth_payload())
        assert r["score"] == 72.5


# =====================================================================
#  3. Normalized error payload
# =====================================================================

class TestErrorPayload:

    def test_error_output_has_all_fields(self):
        r = build_error_output("breadth_participation", "Connection timeout")
        for field in REQUIRED_FIELDS:
            assert field in r, f"missing: {field}"

    def test_error_output_status(self):
        r = build_error_output("breadth_participation", "Connection timeout")
        assert r["engine_status"] == "error"
        assert r["score"] is None
        assert r["label"] == "Error"

    def test_error_output_status_detail(self):
        r = build_error_output("breadth_participation", "timeout",
                                exception_type="ConnectionError")
        sd = r["status_detail"]
        assert sd["normalization_source"] == "error_handler"
        assert "engine_failure" in sd["degraded_reasons"]
        assert "exception:ConnectionError" in sd["degraded_reasons"]

    def test_error_output_summary_has_message(self):
        r = build_error_output("breadth_participation", "API key expired")
        assert "API key expired" in r["summary"]
        assert "API key expired" in r["risks"][0]

    def test_error_output_validates(self):
        r = build_error_output("volatility_options", "crash")
        ok, errors = validate_normalized_output(r)
        assert ok, f"Errors: {errors}"

    def test_error_output_unknown_engine(self):
        r = build_error_output("nonexistent_engine", "fail")
        assert r["engine_key"] == "nonexistent_engine"
        ok, _ = validate_normalized_output(r)
        assert ok

    def test_none_engine_result_status(self):
        """engine_result=None → error status."""
        r = normalize_engine_output("breadth_participation", _none_engine_payload())
        assert r["engine_status"] in ("error", "no_data")

    def test_empty_engine_result_status(self):
        """engine_result={} → error or no_data status."""
        r = normalize_engine_output("breadth_participation", _empty_engine_payload())
        assert r["engine_status"] in ("error", "no_data")

    def test_news_failed_engine_status(self):
        r = normalize_engine_output("news_sentiment", _news_failed_payload())
        assert r["engine_status"] in ("error", "no_data")
        assert r["score"] is None


# =====================================================================
#  4. Malformed raw engine output
# =====================================================================

class TestMalformedInput:

    def test_engine_result_is_string(self):
        """engine_result is a string instead of dict — no crash."""
        payload = {"engine_result": "error string", "data_quality": {},
                    "as_of": datetime.now(timezone.utc).isoformat()}
        r = normalize_engine_output("breadth_participation", payload)
        assert r["engine_status"] in VALID_ENGINE_STATUSES
        ok, _ = validate_normalized_output(r)
        assert ok

    def test_engine_result_is_list(self):
        """engine_result is a list instead of dict — no crash."""
        payload = {"engine_result": [1, 2, 3], "data_quality": {},
                    "as_of": datetime.now(timezone.utc).isoformat()}
        r = normalize_engine_output("breadth_participation", payload)
        ok, _ = validate_normalized_output(r)
        assert ok

    def test_completely_empty_payload(self):
        """Empty dict payload — no crash."""
        r = normalize_engine_output("breadth_participation", {})
        assert r["engine_status"] in VALID_ENGINE_STATUSES
        ok, _ = validate_normalized_output(r)
        assert ok

    def test_data_quality_is_none(self):
        """data_quality=None — no crash."""
        payload = {"engine_result": {"score": 50}, "data_quality": None,
                    "as_of": datetime.now(timezone.utc).isoformat()}
        r = normalize_engine_output("breadth_participation", payload)
        ok, _ = validate_normalized_output(r)
        assert ok

    def test_news_internal_engine_is_string(self):
        """News internal_engine is a string — no crash."""
        payload = {"internal_engine": "bad", "items": [], "macro_context": {},
                    "source_freshness": [], "as_of": "now", "item_count": 0}
        r = normalize_engine_output("news_sentiment", payload)
        ok, _ = validate_normalized_output(r)
        assert ok


# =====================================================================
#  5. Legacy cached payload handling
# =====================================================================

class TestLegacyPayload:

    def test_detect_legacy_no_normalized(self):
        payload = _legacy_cached_payload()
        is_legacy, reasons = detect_legacy_payload(payload)
        assert is_legacy
        assert "missing_normalized_key" in reasons

    def test_detect_legacy_pre_v11_normalized(self):
        payload = _pre_v11_normalized_payload()
        is_legacy, reasons = detect_legacy_payload(payload)
        assert is_legacy
        assert "missing_engine_status" in reasons

    def test_detect_modern_payload(self):
        """A fully normalized payload is not legacy."""
        payload = {
            "normalized": normalize_engine_output(
                "breadth_participation", _good_breadth_payload()
            ),
        }
        is_legacy, reasons = detect_legacy_payload(payload)
        assert not is_legacy

    def test_detect_legacy_non_dict(self):
        is_legacy, reasons = detect_legacy_payload("not_a_dict")
        assert is_legacy

    def test_normalize_legacy_full_contract(self):
        """Legacy normalization produces all required fields."""
        r = normalize_legacy_payload("breadth_participation",
                                      _legacy_cached_payload())
        for field in REQUIRED_FIELDS:
            assert field in r, f"missing: {field}"

    def test_normalize_legacy_preserves_score(self):
        r = normalize_legacy_payload("breadth_participation",
                                      _legacy_cached_payload(score=60.0))
        assert r["score"] == 60.0

    def test_normalize_legacy_preserves_label(self):
        r = normalize_legacy_payload("breadth_participation",
                                      _legacy_cached_payload(label="Mixed"))
        assert r["label"] == "Mixed"

    def test_normalize_legacy_marks_fallback(self):
        r = normalize_legacy_payload("breadth_participation",
                                      _legacy_cached_payload())
        sd = r["status_detail"]
        assert sd["is_fallback"] is True
        assert sd["is_legacy"] is True
        assert sd["normalization_source"] == "legacy_bridge"

    def test_normalize_legacy_validates(self):
        r = normalize_legacy_payload("breadth_participation",
                                      _legacy_cached_payload())
        ok, errors = validate_normalized_output(r)
        assert ok, f"Errors: {errors}"

    def test_normalize_pre_v11_patches_status(self):
        """Pre-v1.1 normalized payload gets engine_status patched in."""
        r = normalize_legacy_payload("breadth_participation",
                                      _pre_v11_normalized_payload())
        assert "engine_status" in r
        assert r["engine_status"] in VALID_ENGINE_STATUSES
        sd = r.get("status_detail", {})
        assert sd.get("is_legacy") is True

    def test_normalize_legacy_non_dict(self):
        r = normalize_legacy_payload("breadth_participation", "not_a_dict")
        assert r["engine_status"] == "error"
        ok, _ = validate_normalized_output(r)
        assert ok


# =====================================================================
#  6. Mixed cache-era payloads (downstream-safe)
# =====================================================================

class TestMixedCacheEra:

    def test_old_and_new_side_by_side(self):
        """Both old and new payloads produce valid normalized outputs."""
        old = normalize_legacy_payload("breadth_participation",
                                        _legacy_cached_payload())
        new = normalize_engine_output("breadth_participation",
                                       _good_breadth_payload())

        # Both must have all required fields
        for field in REQUIRED_FIELDS:
            assert field in old, f"old missing: {field}"
            assert field in new, f"new missing: {field}"

        # Both must validate
        ok_old, _ = validate_normalized_output(old)
        ok_new, _ = validate_normalized_output(new)
        assert ok_old and ok_new

    def test_legacy_can_be_consumed_same_as_modern(self):
        """Downstream code reading common fields works for both."""
        old = normalize_legacy_payload("breadth_participation",
                                        _legacy_cached_payload(score=60.0))
        new = normalize_engine_output("breadth_participation",
                                       _good_breadth_payload())

        for output in (old, new):
            # Common consumption pattern
            score = output["score"]
            label = output["label"]
            status = output["engine_status"]
            summary = output["summary"]
            assert score is None or isinstance(score, (int, float))
            assert isinstance(label, str)
            assert status in VALID_ENGINE_STATUSES
            assert isinstance(summary, str)

    def test_no_crash_on_mixed_dict(self):
        """Assembling results from mixed eras doesn't crash."""
        results = {}
        results["breadth_participation"] = normalize_engine_output(
            "breadth_participation", _good_breadth_payload())
        results["volatility_options"] = normalize_legacy_payload(
            "volatility_options", _legacy_cached_payload())
        results["cross_asset_macro"] = build_error_output(
            "cross_asset_macro", "timeout")

        for key, r in results.items():
            ok, errors = validate_normalized_output(r)
            assert ok, f"{key} failed: {errors}"


# =====================================================================
#  7. Unified contract shape assertions
# =====================================================================

class TestUnifiedContractShape:

    def test_required_fields_constant(self):
        assert "engine_status" in REQUIRED_FIELDS
        assert "status_detail" in REQUIRED_FIELDS
        assert len(REQUIRED_FIELDS) == 25

    def test_all_engines_same_field_set(self):
        """All engines produce the exact same set of top-level keys."""
        outputs = [
            normalize_engine_output("breadth_participation", _good_breadth_payload()),
            normalize_engine_output("volatility_options", _degraded_vol_payload()),
            normalize_engine_output("breadth_participation", _empty_engine_payload()),
            build_error_output("flows_positioning", "crash"),
            normalize_legacy_payload("liquidity_financial_conditions",
                                      _legacy_cached_payload()),
        ]

        for output in outputs:
            for field in REQUIRED_FIELDS:
                assert field in output, f"missing {field} in {output.get('engine_key')}"

    def test_error_and_success_same_keys(self):
        success = normalize_engine_output("breadth_participation",
                                           _good_breadth_payload())
        error = build_error_output("breadth_participation", "fail")
        assert set(success.keys()) == set(error.keys())


# =====================================================================
#  8. validate_normalized_output behavior
# =====================================================================

class TestValidation:

    def test_valid_success(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        ok, errors = validate_normalized_output(r)
        assert ok, errors

    def test_valid_error(self):
        r = build_error_output("breadth_participation", "fail")
        ok, errors = validate_normalized_output(r)
        assert ok, errors

    def test_valid_legacy(self):
        r = normalize_legacy_payload("breadth_participation",
                                      _legacy_cached_payload())
        ok, errors = validate_normalized_output(r)
        assert ok, errors

    def test_not_a_dict(self):
        ok, errors = validate_normalized_output("string")
        assert not ok
        assert "must be a dict" in errors[0]

    def test_missing_field(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        del r["engine_status"]
        ok, errors = validate_normalized_output(r)
        assert not ok
        assert any("engine_status" in e for e in errors)

    def test_invalid_engine_status(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        r["engine_status"] = "banana"
        ok, errors = validate_normalized_output(r)
        assert not ok

    def test_list_field_wrong_type(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        r["bull_factors"] = "not_a_list"
        ok, errors = validate_normalized_output(r)
        assert not ok

    def test_dict_field_wrong_type(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        r["data_quality"] = "not_a_dict"
        ok, errors = validate_normalized_output(r)
        assert not ok

    def test_round_trip_all_scenarios(self):
        """Every production output validates."""
        scenarios = [
            normalize_engine_output("breadth_participation", _good_breadth_payload()),
            normalize_engine_output("volatility_options", _degraded_vol_payload()),
            normalize_engine_output("breadth_participation", _empty_engine_payload()),
            normalize_engine_output("breadth_participation", _none_engine_payload()),
            normalize_engine_output("news_sentiment", _news_failed_payload()),
            build_error_output("breadth_participation", "crash"),
            build_degraded_output("breadth_participation", _good_breadth_payload(),
                                   reasons=["test"]),
            normalize_legacy_payload("breadth_participation",
                                      _legacy_cached_payload()),
            normalize_legacy_payload("breadth_participation",
                                      _pre_v11_normalized_payload()),
        ]
        for i, r in enumerate(scenarios):
            ok, errors = validate_normalized_output(r)
            assert ok, f"Scenario {i} failed: {errors}"


# =====================================================================
#  9. Context assembler fallback integration
# =====================================================================

class TestContextAssemblerFallback:

    def test_fallback_produces_full_contract(self):
        """Context assembler fallback now produces full contract shape."""
        from app.services.context_assembler import _extract_market_module

        payload = _legacy_cached_payload()
        mod, source, warnings = _extract_market_module(
            "breadth_participation", payload)
        assert source == "fallback"
        norm = mod["normalized"]
        for field in REQUIRED_FIELDS:
            assert field in norm, f"fallback missing: {field}"

    def test_fallback_has_status_detail(self):
        from app.services.context_assembler import _extract_market_module

        payload = _legacy_cached_payload()
        mod, _, _ = _extract_market_module("breadth_participation", payload)
        sd = mod["normalized"]["status_detail"]
        assert sd["is_fallback"] is True
        assert sd["is_legacy"] is True

    def test_fallback_validates(self):
        from app.services.context_assembler import _extract_market_module

        payload = _legacy_cached_payload()
        mod, _, _ = _extract_market_module("breadth_participation", payload)
        ok, errors = validate_normalized_output(mod["normalized"])
        assert ok, f"Errors: {errors}"


# =====================================================================
#  10. Engine status derivation edge cases
# =====================================================================

class TestEngineStatusDerivation:

    def test_stale_data_detected_as_degraded(self):
        """Payload with old as_of gets staleness in degraded_reasons."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        payload = {
            "engine_result": {
                "score": 70,
                "label": "Test",
                "signal_quality": "high",
                "as_of": old_time,
            },
            "data_quality": {"signal_quality": "high"},
            "as_of": old_time,
        }
        r = normalize_engine_output("breadth_participation", payload)
        assert r["engine_status"] == "degraded"
        assert any("stale_data" in reason
                    for reason in r["status_detail"]["degraded_reasons"])

    def test_fresh_data_not_stale(self):
        r = normalize_engine_output("breadth_participation", _good_breadth_payload())
        reasons = r["status_detail"]["degraded_reasons"]
        assert not any("stale_data" in reason for reason in reasons)

    def test_no_score_no_warnings_is_no_data(self):
        """Score=None + no warnings/missing → no_data status."""
        payload = {
            "engine_result": {},
            "data_quality": {},
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        r = normalize_engine_output("breadth_participation", payload)
        assert r["engine_status"] == "no_data"

    def test_no_score_with_warnings_is_error(self):
        """Score=None + warnings → error status."""
        payload = {
            "engine_result": {"warnings": ["something broke"],
                              "missing_inputs": ["vix"]},
            "data_quality": {},
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        r = normalize_engine_output("breadth_participation", payload)
        assert r["engine_status"] == "error"
