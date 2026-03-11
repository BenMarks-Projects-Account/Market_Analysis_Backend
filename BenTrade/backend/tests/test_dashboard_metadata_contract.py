"""Tests for dashboard_metadata_contract — shared dashboard data-quality metadata.

Test categories:
  1. Vocabulary validation — frozen status sets contain expected values
  2. Contract shape — build_dashboard_metadata returns all required fields
  3. Field-status classification — classify_field_status priority ordering
  4. Data-quality-status computation — thresholds and edge cases
  5. Coverage-level computation — ratio-based classification
  6. Freshness-status computation — stale / live / recent / unknown
  7. Proxy-reliance computation — ratio-based and proxy_summary input
  8. Confidence-impact structure — factors, actionability
  9. Field-status-map building — signal_provenance, proxy_summary, missing_inputs
 10. Source-status building — merged source_errors + source_freshness
 11. Error payload handling — is_error_payload paths
 12. Integration: clean engine (breadth) — full run with realistic data
 13. Integration: messy engine (flows) — heavy proxy, partial data
 14. Degraded-data proof — distinct representation of each status type
 15. Edge cases — None / empty inputs, unknown engine_key
"""

import pytest

from app.services.dashboard_metadata_contract import (
    FIELD_STATUS_VALUES,
    DATA_QUALITY_STATUSES,
    COVERAGE_LEVELS,
    FRESHNESS_STATUSES,
    PROXY_RELIANCE_LEVELS,
    ENGINE_DASHBOARD_META,
    build_dashboard_metadata,
    classify_field_status,
    validate_field_status,
    _build_field_status_map,
    _collect_failed_sources,
    _build_source_status,
    _compute_data_quality_status,
    _compute_coverage_level,
    _compute_freshness_status,
    _compute_proxy_reliance,
    _build_confidence_impact,
    _get_known_fields_for_engine,
    _apply_news_source_field_status,
    _NEWS_FIELD_SOURCE_DEPS,
)


# ════════════════════════════════════════════════════════════════════════
# 1. VOCABULARY VALIDATION
# ════════════════════════════════════════════════════════════════════════

class TestVocabulary:
    """Frozen vocabularies contain exactly the documented values."""

    def test_field_status_values_complete(self):
        expected = {
            "ok", "proxy_only", "stale", "failed_source",
            "missing_source_data", "insufficient_history", "partial",
            "unimplemented", "degraded", "unknown",
        }
        assert FIELD_STATUS_VALUES == expected

    def test_data_quality_statuses(self):
        expected = {"good", "acceptable", "degraded", "poor", "unavailable"}
        assert DATA_QUALITY_STATUSES == expected

    def test_coverage_levels(self):
        expected = {"full", "high", "partial", "minimal", "none"}
        assert COVERAGE_LEVELS == expected

    def test_freshness_statuses(self):
        expected = {"live", "recent", "stale", "very_stale", "unknown"}
        assert FRESHNESS_STATUSES == expected

    def test_proxy_reliance_levels(self):
        expected = {"none", "low", "moderate", "high", "critical"}
        assert PROXY_RELIANCE_LEVELS == expected

    def test_validate_field_status_valid(self):
        for s in FIELD_STATUS_VALUES:
            assert validate_field_status(s) is True

    def test_validate_field_status_invalid(self):
        assert validate_field_status("bogus") is False
        assert validate_field_status("") is False
        assert validate_field_status("OK") is False  # case-sensitive


# ════════════════════════════════════════════════════════════════════════
# 2. CONTRACT SHAPE — build_dashboard_metadata
# ════════════════════════════════════════════════════════════════════════

REQUIRED_TOP_LEVEL_KEYS = {
    "data_quality_status",
    "coverage_level",
    "freshness_status",
    "proxy_reliance_level",
    "time_horizon",
    "confidence_impact",
    "missing_fields",
    "stale_fields",
    "proxy_fields",
    "failed_sources",
    "insufficient_history_fields",
    "unimplemented_fields",
    "partial_fields",
    "field_status_map",
    "source_status",
    "warnings",
    "notes",
    "last_successful_update",
    "evaluation_metadata",
}

REQUIRED_EVAL_META_KEYS = {
    "evaluated_at", "engine_key", "engine_version", "compute_duration_s",
}

REQUIRED_CONFIDENCE_IMPACT_KEYS = {
    "confidence_score", "signal_quality", "degradation_factors",
    "proxy_reliance_level", "is_actionable",
}


class TestContractShape:
    """build_dashboard_metadata always returns the full contract shape."""

    def test_minimal_call_has_all_required_keys(self):
        result = build_dashboard_metadata("breadth_participation")
        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_evaluation_metadata_shape(self):
        result = build_dashboard_metadata("breadth_participation")
        assert set(result["evaluation_metadata"].keys()) == REQUIRED_EVAL_META_KEYS

    def test_confidence_impact_shape(self):
        result = build_dashboard_metadata("breadth_participation")
        assert set(result["confidence_impact"].keys()) == REQUIRED_CONFIDENCE_IMPACT_KEYS

    def test_list_fields_are_lists(self):
        result = build_dashboard_metadata("breadth_participation")
        for key in ("missing_fields", "stale_fields", "proxy_fields",
                     "failed_sources", "insufficient_history_fields",
                     "unimplemented_fields", "partial_fields",
                     "source_status", "warnings", "notes"):
            assert isinstance(result[key], list), f"{key} should be a list"

    def test_field_status_map_is_dict(self):
        result = build_dashboard_metadata("breadth_participation")
        assert isinstance(result["field_status_map"], dict)

    def test_all_field_status_values_are_valid(self):
        result = build_dashboard_metadata("breadth_participation", engine_result={
            "missing_inputs": ["trend"],
            "confidence_score": 70,
            "signal_quality": "moderate",
        })
        for field, status in result["field_status_map"].items():
            assert validate_field_status(status), f"Invalid status '{status}' for field '{field}'"

    def test_engine_key_in_evaluation_metadata(self):
        result = build_dashboard_metadata("volatility_options")
        assert result["evaluation_metadata"]["engine_key"] == "volatility_options"

    def test_engine_version_populated_for_known_engine(self):
        result = build_dashboard_metadata("breadth_participation")
        assert result["evaluation_metadata"]["engine_version"] == "1.0"

    def test_engine_version_unknown_for_unregistered_engine(self):
        result = build_dashboard_metadata("made_up_engine")
        assert result["evaluation_metadata"]["engine_version"] == "unknown"


# ════════════════════════════════════════════════════════════════════════
# 3. FIELD-STATUS CLASSIFICATION — classify_field_status
# ════════════════════════════════════════════════════════════════════════

class TestClassifyFieldStatus:
    """classify_field_status respects priority ordering."""

    def test_default_is_ok(self):
        assert classify_field_status("some_field") == "ok"

    def test_failed_source_wins_over_all(self):
        assert classify_field_status("f", is_failed_source=True, is_missing=True,
                                     is_proxy=True, is_stale=True) == "failed_source"

    def test_missing_wins_over_proxy_and_stale(self):
        assert classify_field_status("f", is_missing=True, is_proxy=True,
                                     is_stale=True) == "missing_source_data"

    def test_insufficient_history_wins_over_unimplemented(self):
        assert classify_field_status("f", is_insufficient_history=True,
                                     is_unimplemented=True) == "insufficient_history"

    def test_unimplemented_wins_over_stale(self):
        assert classify_field_status("f", is_unimplemented=True,
                                     is_stale=True) == "unimplemented"

    def test_stale_wins_over_proxy(self):
        assert classify_field_status("f", is_stale=True, is_proxy=True) == "stale"

    def test_proxy_wins_over_partial(self):
        assert classify_field_status("f", is_proxy=True, is_partial=True) == "proxy_only"

    def test_partial_alone(self):
        assert classify_field_status("f", is_partial=True) == "partial"

    def test_all_false_returns_ok(self):
        assert classify_field_status("f", is_missing=False, is_proxy=False,
                                     is_stale=False) == "ok"


# ════════════════════════════════════════════════════════════════════════
# 4. DATA-QUALITY-STATUS COMPUTATION
# ════════════════════════════════════════════════════════════════════════

class TestDataQualityStatus:
    """_compute_data_quality_status thresholds."""

    def test_error_payload_returns_unavailable(self):
        assert _compute_data_quality_status(
            confidence_score=85, signal_quality="high",
            missing_count=0, proxy_count=0, failed_source_count=0,
            is_error_payload=True
        ) == "unavailable"

    def test_zero_confidence_returns_unavailable(self):
        assert _compute_data_quality_status(
            confidence_score=0, signal_quality="moderate",
            missing_count=0, proxy_count=0, failed_source_count=0,
            is_error_payload=False
        ) == "unavailable"

    def test_high_confidence_returns_good(self):
        assert _compute_data_quality_status(
            confidence_score=85, signal_quality="high",
            missing_count=0, proxy_count=0, failed_source_count=0,
            is_error_payload=False
        ) == "good"

    def test_moderate_confidence_returns_acceptable(self):
        assert _compute_data_quality_status(
            confidence_score=75, signal_quality="moderate",
            missing_count=0, proxy_count=0, failed_source_count=0,
            is_error_payload=False
        ) == "acceptable"

    def test_low_confidence_returns_degraded(self):
        assert _compute_data_quality_status(
            confidence_score=55, signal_quality="moderate",
            missing_count=0, proxy_count=0, failed_source_count=0,
            is_error_payload=False
        ) == "degraded"

    def test_very_low_confidence_returns_poor(self):
        assert _compute_data_quality_status(
            confidence_score=30, signal_quality="moderate",
            missing_count=0, proxy_count=0, failed_source_count=0,
            is_error_payload=False
        ) == "poor"

    def test_many_failed_sources_returns_poor(self):
        assert _compute_data_quality_status(
            confidence_score=70, signal_quality="moderate",
            missing_count=0, proxy_count=0, failed_source_count=3,
            is_error_payload=False
        ) == "poor"

    def test_two_failed_sources_returns_degraded(self):
        assert _compute_data_quality_status(
            confidence_score=85, signal_quality="high",
            missing_count=0, proxy_count=0, failed_source_count=2,
            is_error_payload=False
        ) == "degraded"

    def test_many_proxies_returns_degraded(self):
        assert _compute_data_quality_status(
            confidence_score=85, signal_quality="high",
            missing_count=0, proxy_count=4, failed_source_count=0,
            is_error_payload=False
        ) == "degraded"

    def test_low_quality_many_missing_returns_poor(self):
        assert _compute_data_quality_status(
            confidence_score=85, signal_quality="low",
            missing_count=4, proxy_count=0, failed_source_count=0,
            is_error_payload=False
        ) == "poor"


# ════════════════════════════════════════════════════════════════════════
# 5. COVERAGE-LEVEL COMPUTATION
# ════════════════════════════════════════════════════════════════════════

class TestCoverageLevel:
    """_compute_coverage_level ratio thresholds."""

    def test_full_coverage(self):
        fsm = {"a": "ok", "b": "ok", "c": "ok"}
        assert _compute_coverage_level(field_status_map=fsm, missing_count=0,
                                       unimplemented_count=0) == "full"

    def test_high_coverage(self):
        fsm = {"a": "ok", "b": "ok", "c": "ok", "d": "ok", "e": "missing_source_data"}
        assert _compute_coverage_level(field_status_map=fsm, missing_count=1,
                                       unimplemented_count=0) == "high"

    def test_partial_coverage(self):
        fsm = {str(i): "ok" for i in range(6)}
        fsm.update({"m1": "missing_source_data", "m2": "missing_source_data", "m3": "unimplemented"})
        assert _compute_coverage_level(field_status_map=fsm, missing_count=2,
                                       unimplemented_count=1) in ("partial", "high")

    def test_minimal_coverage(self):
        fsm = {"a": "ok", "b": "missing_source_data", "c": "missing_source_data", "d": "unimplemented"}
        assert _compute_coverage_level(field_status_map=fsm, missing_count=2,
                                       unimplemented_count=1) == "minimal"

    def test_none_coverage_all_missing(self):
        fsm = {"a": "missing_source_data", "b": "unimplemented"}
        assert _compute_coverage_level(field_status_map=fsm, missing_count=1,
                                       unimplemented_count=1) == "none"

    def test_empty_field_status_map(self):
        assert _compute_coverage_level(field_status_map={}, missing_count=0,
                                       unimplemented_count=0) == "none"


# ════════════════════════════════════════════════════════════════════════
# 6. FRESHNESS-STATUS COMPUTATION
# ════════════════════════════════════════════════════════════════════════

class TestFreshnessStatus:
    """_compute_freshness_status thresholds."""

    def test_very_stale(self):
        assert _compute_freshness_status(source_freshness=None, stale_count=3,
                                         compute_duration_s=1.0) == "very_stale"

    def test_stale(self):
        assert _compute_freshness_status(source_freshness=None, stale_count=1,
                                         compute_duration_s=None) == "stale"

    def test_live(self):
        assert _compute_freshness_status(source_freshness=None, stale_count=0,
                                         compute_duration_s=0.5) == "live"

    def test_recent_with_source_freshness(self):
        sf = [{"source": "finnhub", "status": "ok"}]
        assert _compute_freshness_status(source_freshness=sf, stale_count=0,
                                         compute_duration_s=None) == "recent"

    def test_unknown_no_info(self):
        assert _compute_freshness_status(source_freshness=None, stale_count=0,
                                         compute_duration_s=None) == "unknown"


# ════════════════════════════════════════════════════════════════════════
# 7. PROXY-RELIANCE COMPUTATION
# ════════════════════════════════════════════════════════════════════════

class TestProxyReliance:
    """_compute_proxy_reliance ratio thresholds."""

    def test_none_reliance(self):
        assert _compute_proxy_reliance(proxy_count=0, total_fields=10,
                                       proxy_summary=None) == "none"

    def test_low_reliance(self):
        assert _compute_proxy_reliance(proxy_count=1, total_fields=10,
                                       proxy_summary=None) == "low"

    def test_moderate_reliance(self):
        assert _compute_proxy_reliance(proxy_count=3, total_fields=10,
                                       proxy_summary=None) == "moderate"

    def test_high_reliance(self):
        assert _compute_proxy_reliance(proxy_count=5, total_fields=10,
                                       proxy_summary=None) == "high"

    def test_critical_reliance(self):
        assert _compute_proxy_reliance(proxy_count=8, total_fields=10,
                                       proxy_summary=None) == "critical"


# ════════════════════════════════════════════════════════════════════════
# 8. CONFIDENCE-IMPACT STRUCTURE
# ════════════════════════════════════════════════════════════════════════

class TestConfidenceImpact:
    """_build_confidence_impact structure and factors."""

    def test_clean_engine_no_factors(self):
        ci = _build_confidence_impact(
            confidence_score=85, signal_quality="high",
            proxy_reliance_level="none", missing_count=0,
            stale_count=0, failed_source_count=0,
        )
        assert ci["confidence_score"] == 85
        assert ci["signal_quality"] == "high"
        assert ci["degradation_factors"] == []
        assert ci["is_actionable"] is True

    def test_degraded_engine_lists_factors(self):
        ci = _build_confidence_impact(
            confidence_score=55, signal_quality="moderate",
            proxy_reliance_level="high", missing_count=2,
            stale_count=1, failed_source_count=1,
        )
        assert len(ci["degradation_factors"]) == 4
        assert ci["is_actionable"] is True  # 55 >= 40 and moderate != low

    def test_low_quality_not_actionable(self):
        ci = _build_confidence_impact(
            confidence_score=30, signal_quality="low",
            proxy_reliance_level="critical", missing_count=5,
            stale_count=3, failed_source_count=2,
        )
        assert ci["is_actionable"] is False

    def test_zero_confidence_not_actionable(self):
        ci = _build_confidence_impact(
            confidence_score=0, signal_quality="moderate",
            proxy_reliance_level="none", missing_count=0,
            stale_count=0, failed_source_count=0,
        )
        assert ci["is_actionable"] is False


# ════════════════════════════════════════════════════════════════════════
# 9. FIELD-STATUS-MAP BUILDING
# ════════════════════════════════════════════════════════════════════════

class TestFieldStatusMap:
    """_build_field_status_map from various input shapes."""

    def test_signal_provenance_proxy_tagged(self):
        sp = {
            "vix": {"type": "direct", "source": "tradier"},
            "put_call_ratio": {"type": "proxy", "source": "vix_derived"},
        }
        fsm = _build_field_status_map(
            engine_key="volatility_options",
            missing_inputs=[],
            signal_provenance=sp,
            proxy_summary=None,
            source_errors=None,
            source_freshness=None,
            raw_data_meta=None,
            is_error_payload=False,
        )
        assert fsm["vix"] == "ok"
        assert fsm["put_call_ratio"] == "proxy_only"

    def test_missing_input_maps_to_missing_source_data(self):
        fsm = _build_field_status_map(
            engine_key="breadth_participation",
            missing_inputs=["trend", "volume"],
            signal_provenance=None,
            proxy_summary=None,
            source_errors=None,
            source_freshness=None,
            raw_data_meta=None,
            is_error_payload=False,
        )
        assert fsm["trend"] == "missing_source_data"
        assert fsm["volume"] == "missing_source_data"

    def test_failed_source_in_provenance(self):
        sp = {"copper": {"type": "direct", "source": "fred"}}
        fsm = _build_field_status_map(
            engine_key="cross_asset_macro",
            missing_inputs=["copper"],
            signal_provenance=sp,
            proxy_summary=None,
            source_errors={"fred": "timeout"},
            source_freshness=None,
            raw_data_meta=None,
            is_error_payload=False,
        )
        assert fsm["copper"] == "failed_source"

    def test_stale_warning_in_provenance(self):
        sp = {"copper_hg": {"type": "direct", "source": "fred", "stale_warning": True}}
        fsm = _build_field_status_map(
            engine_key="cross_asset_macro",
            missing_inputs=[],
            signal_provenance=sp,
            proxy_summary=None,
            source_errors=None,
            source_freshness=None,
            raw_data_meta=None,
            is_error_payload=False,
        )
        assert fsm["copper_hg"] == "stale"

    def test_proxy_summary_fallback(self):
        ps = {
            "proxy_signal_names": ["vix_proxy", "flow_proxy"],
            "direct_signal_names": ["spy_price"],
        }
        fsm = _build_field_status_map(
            engine_key="flows_positioning",
            missing_inputs=["vix_proxy"],
            signal_provenance=None,
            proxy_summary=ps,
            source_errors=None,
            source_freshness=None,
            raw_data_meta=None,
            is_error_payload=False,
        )
        assert fsm["vix_proxy"] == "missing_source_data"
        assert fsm["flow_proxy"] == "proxy_only"
        assert fsm["spy_price"] == "ok"

    def test_known_fields_filled_as_ok(self):
        fsm = _build_field_status_map(
            engine_key="breadth_participation",
            missing_inputs=[],
            signal_provenance=None,
            proxy_summary=None,
            source_errors=None,
            source_freshness=None,
            raw_data_meta=None,
            is_error_payload=False,
        )
        known = _get_known_fields_for_engine("breadth_participation")
        for f in known:
            assert fsm[f] == "ok", f"expected '{f}' to be 'ok'"

    def test_error_payload_all_failed_source(self):
        fsm = _build_field_status_map(
            engine_key="liquidity_financial_conditions",
            missing_inputs=[],
            signal_provenance=None,
            proxy_summary=None,
            source_errors=None,
            source_freshness=None,
            raw_data_meta=None,
            is_error_payload=True,
        )
        for f, status in fsm.items():
            assert status == "failed_source", f"expected '{f}' to be 'failed_source'"


# ════════════════════════════════════════════════════════════════════════
# 10. SOURCE-STATUS BUILDING
# ════════════════════════════════════════════════════════════════════════

class TestSourceStatus:
    """_build_source_status merges source_errors and source_freshness."""

    def test_source_freshness_only(self):
        sf = [
            {"source": "finnhub", "status": "ok", "last_fetched": "2026-03-15", "item_count": 10},
            {"source": "polygon", "status": "error", "error": "rate_limit"},
        ]
        result = _build_source_status(None, sf)
        assert len(result) == 2
        assert result[0]["source"] == "finnhub"
        assert result[0]["status"] == "ok"
        assert result[1]["status"] == "error"

    def test_source_errors_only(self):
        se = {"fred": "connection_timeout", "tradier": "auth_failed"}
        result = _build_source_status(se, None)
        assert len(result) == 2
        sources = {r["source"] for r in result}
        assert sources == {"fred", "tradier"}
        for r in result:
            assert r["status"] == "error"

    def test_deduplication(self):
        sf = [{"source": "fred", "status": "error", "error": "rate_limit"}]
        se = {"fred": "also_failed"}
        result = _build_source_status(se, sf)
        # source_freshness takes precedence, source_errors deduped
        fred_entries = [r for r in result if r["source"] == "fred"]
        assert len(fred_entries) == 1

    def test_empty_inputs(self):
        assert _build_source_status(None, None) == []


# ════════════════════════════════════════════════════════════════════════
# 11. FAILED SOURCES COLLECTION
# ════════════════════════════════════════════════════════════════════════

class TestCollectFailedSources:
    """_collect_failed_sources merging."""

    def test_from_source_errors(self):
        result = _collect_failed_sources({"fred": "timeout"}, None)
        assert len(result) == 1
        assert result[0]["source"] == "fred"

    def test_from_source_freshness_error(self):
        sf = [{"source": "polygon", "status": "error"}]
        result = _collect_failed_sources(None, sf)
        assert len(result) == 1
        assert result[0]["source"] == "polygon"

    def test_ok_source_freshness_not_included(self):
        sf = [{"source": "finnhub", "status": "ok"}]
        result = _collect_failed_sources(None, sf)
        assert len(result) == 0

    def test_deduplication_across_both(self):
        se = {"fred": "timeout"}
        sf = [{"source": "fred", "status": "error"}]
        result = _collect_failed_sources(se, sf)
        assert len(result) == 1

    def test_error_message_truncated(self):
        long_msg = "x" * 500
        result = _collect_failed_sources({"src": long_msg}, None)
        assert len(result[0]["error"]) <= 200


# ════════════════════════════════════════════════════════════════════════
# 12. ERROR PAYLOAD HANDLING
# ════════════════════════════════════════════════════════════════════════

class TestErrorPayload:
    """Error payload paths produce correct metadata."""

    def test_error_payload_data_quality_unavailable(self):
        result = build_dashboard_metadata(
            "breadth_participation",
            is_error_payload=True,
            error_stage="data_fetch",
        )
        assert result["data_quality_status"] == "unavailable"

    def test_error_payload_has_note(self):
        result = build_dashboard_metadata(
            "breadth_participation",
            is_error_payload=True,
            error_stage="engine",
        )
        assert any("Engine failed at stage: engine" in n for n in result["notes"])

    def test_error_payload_last_successful_update_none(self):
        result = build_dashboard_metadata(
            "breadth_participation",
            is_error_payload=True,
        )
        assert result["last_successful_update"] is None

    def test_error_payload_all_fields_failed(self):
        result = build_dashboard_metadata(
            "volatility_options",
            is_error_payload=True,
        )
        for f, status in result["field_status_map"].items():
            assert status == "failed_source"


# ════════════════════════════════════════════════════════════════════════
# 13. INTEGRATION: CLEAN ENGINE (breadth)
# ════════════════════════════════════════════════════════════════════════

class TestIntegrationCleanEngine:
    """Full integration with realistic breadth engine output."""

    @pytest.fixture
    def breadth_engine_result(self):
        return {
            "engine": "breadth_participation",
            "as_of": "2026-03-15T14:00:00Z",
            "score": 72.5,
            "label": "Healthy Breadth",
            "confidence_score": 85.0,
            "signal_quality": "high",
            "warnings": [],
            "missing_inputs": [],
            "diagnostics": {
                "compute_duration_s": 0.45,
            },
        }

    def test_clean_engine_good_quality(self, breadth_engine_result):
        md = build_dashboard_metadata(
            "breadth_participation",
            engine_result=breadth_engine_result,
            compute_duration_s=0.45,
        )
        assert md["data_quality_status"] == "good"
        assert md["coverage_level"] == "full"
        assert md["freshness_status"] == "live"
        assert md["proxy_reliance_level"] == "none"
        assert md["missing_fields"] == []
        assert md["stale_fields"] == []
        assert md["proxy_fields"] == []
        assert md["failed_sources"] == []

    def test_clean_engine_contract_shape(self, breadth_engine_result):
        md = build_dashboard_metadata(
            "breadth_participation",
            engine_result=breadth_engine_result,
        )
        assert set(md.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_clean_engine_confidence_actionable(self, breadth_engine_result):
        md = build_dashboard_metadata(
            "breadth_participation",
            engine_result=breadth_engine_result,
        )
        assert md["confidence_impact"]["is_actionable"] is True
        assert md["confidence_impact"]["degradation_factors"] == []

    def test_clean_engine_field_map_all_ok(self, breadth_engine_result):
        md = build_dashboard_metadata(
            "breadth_participation",
            engine_result=breadth_engine_result,
        )
        for field, status in md["field_status_map"].items():
            assert status == "ok", f"field '{field}' unexpectedly '{status}'"

    def test_clean_engine_last_successful_update(self, breadth_engine_result):
        md = build_dashboard_metadata(
            "breadth_participation",
            engine_result=breadth_engine_result,
        )
        assert md["last_successful_update"] == "2026-03-15T14:00:00Z"


# ════════════════════════════════════════════════════════════════════════
# 14. INTEGRATION: MESSY ENGINE (flows — heavy proxy)
# ════════════════════════════════════════════════════════════════════════

class TestIntegrationMessyEngine:
    """Full integration with flows engine: heavy proxy, missing data."""

    @pytest.fixture
    def flows_engine_result(self):
        return {
            "engine": "flows_positioning",
            "as_of": "2026-03-15T14:00:00Z",
            "score": 45.0,
            "label": "Elevated Positioning Risk",
            "confidence_score": 52.0,
            "signal_quality": "moderate",
            "warnings": [
                "VIX put/call ratio using proxy: options volume from Tradier",
                "Gamma exposure using proxy: options OI from Tradier",
                "Dark pool ratio using proxy: generic volume",
            ],
            "missing_inputs": ["cot_data", "13f_data"],
            "diagnostics": {
                "compute_duration_s": 1.2,
                "signal_provenance": {
                    "vix_put_call": {"type": "proxy", "source": "tradier_options"},
                    "gamma_exposure": {"type": "proxy", "source": "tradier_oi"},
                    "dark_pool_ratio": {"type": "proxy", "source": "volume"},
                    "options_volume": {"type": "direct", "source": "tradier"},
                    "spy_price": {"type": "direct", "source": "tradier"},
                    "put_call_ratio": {"type": "proxy", "source": "tradier_options"},
                },
                "proxy_summary": {
                    "total_proxy_signals": 4,
                    "proxy_signal_names": ["vix_put_call", "gamma_exposure",
                                           "dark_pool_ratio", "put_call_ratio"],
                    "direct_signal_names": ["options_volume", "spy_price"],
                },
            },
        }

    @pytest.fixture
    def flows_source_errors(self):
        return {"cftc": "CFTC COT data unavailable"}

    def test_flows_degraded_quality(self, flows_engine_result, flows_source_errors):
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result=flows_engine_result,
            source_errors=flows_source_errors,
            compute_duration_s=1.2,
        )
        assert md["data_quality_status"] in ("degraded", "poor")

    def test_flows_has_proxy_fields(self, flows_engine_result):
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result=flows_engine_result,
        )
        assert len(md["proxy_fields"]) >= 3
        assert "vix_put_call" in md["proxy_fields"]

    def test_flows_has_missing_fields(self, flows_engine_result):
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result=flows_engine_result,
        )
        assert "cot_data" in md["missing_fields"]
        assert "13f_data" in md["missing_fields"]

    def test_flows_has_failed_source(self, flows_engine_result, flows_source_errors):
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result=flows_engine_result,
            source_errors=flows_source_errors,
        )
        assert len(md["failed_sources"]) >= 1
        src_names = [fs["source"] for fs in md["failed_sources"]]
        assert "cftc" in src_names

    def test_flows_proxy_reliance_not_none(self, flows_engine_result):
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result=flows_engine_result,
        )
        assert md["proxy_reliance_level"] != "none"

    def test_flows_confidence_has_factors(self, flows_engine_result, flows_source_errors):
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result=flows_engine_result,
            source_errors=flows_source_errors,
        )
        assert len(md["confidence_impact"]["degradation_factors"]) >= 1

    def test_flows_warnings_forwarded(self, flows_engine_result):
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result=flows_engine_result,
        )
        assert len(md["warnings"]) == 3

    def test_flows_contract_shape(self, flows_engine_result):
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result=flows_engine_result,
        )
        assert set(md.keys()) == REQUIRED_TOP_LEVEL_KEYS


# ════════════════════════════════════════════════════════════════════════
# 15. DEGRADED-DATA PROOF — distinct representation per status
# ════════════════════════════════════════════════════════════════════════

class TestDegradedDataProof:
    """Each field status produces a distinct, distinguishable representation.

    This ensures no two quality states can be confused for each other.
    """

    def test_proxy_only_not_confused_with_ok(self):
        sp = {
            "direct_field": {"type": "direct", "source": "tradier"},
            "proxy_field": {"type": "proxy", "source": "derived"},
        }
        md = build_dashboard_metadata(
            "volatility_options",
            engine_result={"confidence_score": 80, "signal_quality": "high",
                          "warnings": [], "missing_inputs": [],
                          "diagnostics": {"signal_provenance": sp}},
        )
        assert md["field_status_map"]["direct_field"] == "ok"
        assert md["field_status_map"]["proxy_field"] == "proxy_only"
        assert "proxy_field" in md["proxy_fields"]
        assert "direct_field" not in md["proxy_fields"]

    def test_stale_not_confused_with_proxy(self):
        sp = {
            "stale_field": {"type": "direct", "source": "fred", "stale_warning": True},
            "proxy_field": {"type": "proxy", "source": "derived"},
        }
        md = build_dashboard_metadata(
            "cross_asset_macro",
            engine_result={"confidence_score": 70, "signal_quality": "moderate",
                          "warnings": [], "missing_inputs": [],
                          "diagnostics": {"signal_provenance": sp}},
        )
        assert md["field_status_map"]["stale_field"] == "stale"
        assert md["field_status_map"]["proxy_field"] == "proxy_only"
        assert "stale_field" in md["stale_fields"]
        assert "stale_field" not in md["proxy_fields"]

    def test_failed_source_not_confused_with_missing(self):
        sp = {
            "fred_field": {"type": "direct", "source": "fred"},
            "other_missing": {"type": "direct", "source": "internal"},
        }
        md = build_dashboard_metadata(
            "cross_asset_macro",
            engine_result={"confidence_score": 60, "signal_quality": "moderate",
                          "warnings": [], "missing_inputs": ["fred_field", "other_missing"],
                          "diagnostics": {"signal_provenance": sp}},
            source_errors={"fred": "connection_timeout"},
        )
        assert md["field_status_map"]["fred_field"] == "failed_source"
        assert md["field_status_map"]["other_missing"] == "missing_source_data"

    def test_all_statuses_distinguishable_in_output(self):
        """Build a synthetic payload containing every status type and verify
        each appears in its own dedicated list field."""
        sp = {
            "ok_sig": {"type": "direct", "source": "tradier"},
            "proxy_sig": {"type": "proxy", "source": "derived"},
            "stale_sig": {"type": "direct", "source": "fred", "stale_warning": True},
            "failed_sig": {"type": "direct", "source": "bad_api"},
        }
        md = build_dashboard_metadata(
            "cross_asset_macro",
            engine_result={
                "confidence_score": 50, "signal_quality": "moderate",
                "warnings": ["test warning"],
                "missing_inputs": ["failed_sig", "missing_sig"],
                "diagnostics": {"signal_provenance": sp},
            },
            source_errors={"bad_api": "500 error"},
        )
        fsm = md["field_status_map"]
        assert fsm["ok_sig"] == "ok"
        assert fsm["proxy_sig"] == "proxy_only"
        assert fsm["stale_sig"] == "stale"
        assert fsm["failed_sig"] == "failed_source"
        assert fsm["missing_sig"] == "missing_source_data"

        # Each status points to distinct list
        assert "proxy_sig" in md["proxy_fields"]
        assert "stale_sig" in md["stale_fields"]
        assert "failed_sig" in md["missing_fields"]  # failed_source is in missing_fields
        assert "missing_sig" in md["missing_fields"]

    def test_high_proxy_noted_in_notes(self):
        """When proxy reliance is high/critical, notes field records it."""
        sp = {f"proxy_{i}": {"type": "proxy", "source": "derived"} for i in range(8)}
        sp.update({f"direct_{i}": {"type": "direct", "source": "tradier"} for i in range(2)})
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result={
                "confidence_score": 40, "signal_quality": "moderate",
                "warnings": [], "missing_inputs": [],
                "diagnostics": {"signal_provenance": sp},
            },
        )
        assert md["proxy_reliance_level"] in ("high", "critical")
        assert any("proxy" in n.lower() for n in md["notes"])


# ════════════════════════════════════════════════════════════════════════
# 16. EDGE CASES
# ════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases: None/empty inputs, unknown engine."""

    def test_none_engine_result(self):
        md = build_dashboard_metadata("breadth_participation", engine_result=None)
        assert set(md.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_empty_engine_result(self):
        md = build_dashboard_metadata("breadth_participation", engine_result={})
        assert set(md.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_unknown_engine_key(self):
        md = build_dashboard_metadata("does_not_exist")
        assert set(md.keys()) == REQUIRED_TOP_LEVEL_KEYS
        assert md["evaluation_metadata"]["engine_key"] == "does_not_exist"

    def test_compute_duration_in_metadata(self):
        md = build_dashboard_metadata("breadth_participation", compute_duration_s=1.23)
        assert md["evaluation_metadata"]["compute_duration_s"] == 1.23

    def test_none_compute_duration(self):
        md = build_dashboard_metadata("breadth_participation")
        assert md["evaluation_metadata"]["compute_duration_s"] is None

    def test_all_six_engines_produce_valid_shape(self):
        for engine_key in ENGINE_DASHBOARD_META:
            md = build_dashboard_metadata(engine_key)
            assert set(md.keys()) == REQUIRED_TOP_LEVEL_KEYS, f"Shape mismatch for {engine_key}"
            for f, s in md["field_status_map"].items():
                assert validate_field_status(s), f"Bad status '{s}' in {engine_key}.{f}"

    def test_source_freshness_unavailable_status_treated_as_failure(self):
        sf = [{"source": "polygon", "status": "unavailable"}]
        failed = _collect_failed_sources(None, sf)
        assert len(failed) == 1
        assert failed[0]["source"] == "polygon"

    def test_known_fields_for_all_engines(self):
        for engine_key in ENGINE_DASHBOARD_META:
            fields = _get_known_fields_for_engine(engine_key)
            assert len(fields) >= 5, f"Too few known fields for {engine_key}"
            # Common fields present
            assert "score" in fields
            assert "label" in fields

    def test_field_status_map_values_all_valid_vocabulary(self):
        """Any field_status_map from any engine must use valid vocabulary."""
        for engine_key in ENGINE_DASHBOARD_META:
            md = build_dashboard_metadata(
                engine_key,
                engine_result={
                    "confidence_score": 70,
                    "signal_quality": "moderate",
                    "missing_inputs": ["some_field"],
                    "warnings": [],
                },
            )
            for f, s in md["field_status_map"].items():
                assert s in FIELD_STATUS_VALUES, f"{engine_key}: field '{f}' has invalid status '{s}'"


# ════════════════════════════════════════════════════════════════════════
# 17. NEWS KNOWN FIELDS EXPANSION (v1.1)
# ════════════════════════════════════════════════════════════════════════

class TestNewsKnownFieldsExpansion:
    """News engine known fields match the 6 compute_engine_scores components."""

    NEWS_EXPECTED_COMPONENTS = [
        "headline_sentiment", "negative_pressure", "narrative_severity",
        "source_agreement", "macro_stress", "recency_pressure",
    ]

    def test_news_known_fields_include_all_components(self):
        fields = _get_known_fields_for_engine("news_sentiment")
        for comp in self.NEWS_EXPECTED_COMPONENTS:
            assert comp in fields, f"Missing news component field: {comp}"

    def test_news_known_fields_include_common(self):
        fields = _get_known_fields_for_engine("news_sentiment")
        for common in ("score", "label", "confidence_score", "signal_quality", "summary"):
            assert common in fields, f"Missing common field: {common}"

    def test_news_known_fields_count(self):
        fields = _get_known_fields_for_engine("news_sentiment")
        # 5 common + 6 components = 11
        assert len(fields) == 11

    def test_news_field_status_map_has_all_components(self):
        md = build_dashboard_metadata("news_sentiment")
        for comp in self.NEWS_EXPECTED_COMPONENTS:
            assert comp in md["field_status_map"], f"field_status_map missing: {comp}"

    def test_news_clean_all_ok(self):
        """All news fields are 'ok' when no failures."""
        md = build_dashboard_metadata(
            "news_sentiment",
            engine_result={"confidence_score": 80, "signal_quality": "high",
                          "warnings": [], "missing_inputs": []},
            compute_duration_s=0.5,
        )
        for comp in self.NEWS_EXPECTED_COMPONENTS:
            assert md["field_status_map"][comp] == "ok"

    def test_removed_macro_context_field(self):
        """'macro_context' is no longer a tracked known field for news."""
        fields = _get_known_fields_for_engine("news_sentiment")
        assert "macro_context" not in fields


# ════════════════════════════════════════════════════════════════════════
# 18. NEWS SOURCE-FIELD DEPENDENCY MAPPING
# ════════════════════════════════════════════════════════════════════════

class TestNewsSourceFieldDeps:
    """_NEWS_FIELD_SOURCE_DEPS and _apply_news_source_field_status."""

    def test_all_news_component_fields_have_deps(self):
        """Every news component field has a source dependency entry."""
        news_specific = [
            "headline_sentiment", "negative_pressure", "narrative_severity",
            "source_agreement", "macro_stress", "recency_pressure",
        ]
        for f in news_specific:
            assert f in _NEWS_FIELD_SOURCE_DEPS, f"No source dep for {f}"

    def test_headline_components_depend_on_news_sources(self):
        for field in ("headline_sentiment", "negative_pressure",
                      "narrative_severity", "source_agreement", "recency_pressure"):
            assert set(_NEWS_FIELD_SOURCE_DEPS[field]) == {"finnhub", "polygon"}

    def test_macro_stress_depends_on_fred(self):
        assert _NEWS_FIELD_SOURCE_DEPS["macro_stress"] == ["fred"]

    def test_both_news_sources_fail_degrades_headline_fields(self):
        fsm: dict[str, str] = {}
        sf = [
            {"source": "finnhub", "status": "error"},
            {"source": "polygon", "status": "error"},
        ]
        _apply_news_source_field_status(fsm, sf)
        for f in ("headline_sentiment", "negative_pressure", "narrative_severity",
                   "source_agreement", "recency_pressure"):
            assert fsm[f] == "degraded", f"Expected degraded for {f}"
        # macro_stress not affected
        assert "macro_stress" not in fsm

    def test_only_one_news_source_fails_no_degradation(self):
        fsm: dict[str, str] = {}
        sf = [
            {"source": "finnhub", "status": "error"},
            {"source": "polygon", "status": "ok"},
        ]
        _apply_news_source_field_status(fsm, sf)
        # Not all deps failed → should not be classified
        assert "headline_sentiment" not in fsm

    def test_fred_failure_degrades_macro_stress(self):
        fsm: dict[str, str] = {}
        sf = [{"source": "fred", "status": "error"}]
        _apply_news_source_field_status(fsm, sf)
        assert fsm["macro_stress"] == "degraded"

    def test_unavailable_source_treated_as_failure(self):
        fsm: dict[str, str] = {}
        sf = [
            {"source": "finnhub", "status": "unavailable"},
            {"source": "polygon", "status": "unavailable"},
        ]
        _apply_news_source_field_status(fsm, sf)
        assert fsm["headline_sentiment"] == "degraded"

    def test_already_classified_field_not_overwritten(self):
        fsm: dict[str, str] = {"headline_sentiment": "failed_source"}
        sf = [
            {"source": "finnhub", "status": "error"},
            {"source": "polygon", "status": "error"},
        ]
        _apply_news_source_field_status(fsm, sf)
        # Should NOT overwrite existing classification
        assert fsm["headline_sentiment"] == "failed_source"

    def test_no_source_freshness_is_noop(self):
        fsm: dict[str, str] = {}
        _apply_news_source_field_status(fsm, [])
        assert fsm == {}


# ════════════════════════════════════════════════════════════════════════
# 19. NEWS FAILURE-PATH PARITY
# ════════════════════════════════════════════════════════════════════════

class TestNewsFailurePathParity:
    """News & Sentiment failure paths produce metadata with
    the same shape and vocabulary as other engines (parity)."""

    @pytest.fixture
    def news_source_freshness(self):
        return [
            {"source": "finnhub", "status": "ok", "last_fetched": "2026-03-15T14:00:00Z",
             "item_count": 20, "error": None},
            {"source": "polygon", "status": "error", "last_fetched": None,
             "item_count": 0, "error": "rate_limit"},
            {"source": "tradier", "status": "unavailable",
             "error": "No news endpoints available"},
            {"source": "fred", "status": "ok", "last_fetched": "2026-03-15T14:00:00Z",
             "item_count": 4, "error": None},
        ]

    @pytest.fixture
    def news_engine_result(self):
        return {
            "score": 62.5,
            "regime_label": "Neutral",
            "components": {
                "headline_sentiment": {"score": 55.0, "signals": [], "inputs": []},
                "negative_pressure": {"score": 70.0, "signals": [], "inputs": []},
                "narrative_severity": {"score": 80.0, "signals": [], "inputs": []},
                "source_agreement": {"score": 45.0, "signals": [], "inputs": []},
                "macro_stress": {"score": 60.0, "signals": [], "inputs": []},
                "recency_pressure": {"score": 50.0, "signals": [], "inputs": []},
            },
            "as_of": "2026-03-15T14:00:00Z",
            "confidence_score": 70,
            "signal_quality": "moderate",
            "warnings": [],
            "missing_inputs": [],
        }

    def test_news_with_source_freshness_shape(self, news_engine_result, news_source_freshness):
        md = build_dashboard_metadata(
            "news_sentiment",
            engine_result=news_engine_result,
            source_freshness=news_source_freshness,
            compute_duration_s=0.8,
        )
        assert set(md.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_news_source_status_populated(self, news_engine_result, news_source_freshness):
        md = build_dashboard_metadata(
            "news_sentiment",
            engine_result=news_engine_result,
            source_freshness=news_source_freshness,
        )
        assert len(md["source_status"]) == 4
        sources = {s["source"] for s in md["source_status"]}
        assert sources == {"finnhub", "polygon", "tradier", "fred"}

    def test_news_failed_sources_includes_error_and_unavailable(
        self, news_engine_result, news_source_freshness
    ):
        md = build_dashboard_metadata(
            "news_sentiment",
            engine_result=news_engine_result,
            source_freshness=news_source_freshness,
        )
        failed_names = {fs["source"] for fs in md["failed_sources"]}
        assert "polygon" in failed_names
        assert "tradier" in failed_names

    def test_news_compute_duration_in_metadata(self, news_engine_result, news_source_freshness):
        md = build_dashboard_metadata(
            "news_sentiment",
            engine_result=news_engine_result,
            source_freshness=news_source_freshness,
            compute_duration_s=1.23,
        )
        assert md["evaluation_metadata"]["compute_duration_s"] == 1.23

    def test_news_freshness_status_live_with_duration(self, news_engine_result, news_source_freshness):
        md = build_dashboard_metadata(
            "news_sentiment",
            engine_result=news_engine_result,
            source_freshness=news_source_freshness,
            compute_duration_s=0.5,
        )
        assert md["freshness_status"] in ("live", "recent")

    def test_news_source_errors_passed_alongside_freshness(self, news_engine_result, news_source_freshness):
        """When source_errors is derived from freshness and passed,
        failed sources appear in both failed_sources and source_status."""
        source_errors = {"polygon": "rate_limit"}
        md = build_dashboard_metadata(
            "news_sentiment",
            engine_result=news_engine_result,
            source_errors=source_errors,
            source_freshness=news_source_freshness,
            compute_duration_s=0.5,
        )
        failed_names = {fs["source"] for fs in md["failed_sources"]}
        assert "polygon" in failed_names

    def test_news_all_sources_fail_produces_degraded(self):
        sf = [
            {"source": "finnhub", "status": "error", "error": "timeout"},
            {"source": "polygon", "status": "error", "error": "rate_limit"},
            {"source": "tradier", "status": "unavailable", "error": "no endpoint"},
            {"source": "fred", "status": "error", "error": "connection refused"},
        ]
        se = {"finnhub": "timeout", "polygon": "rate_limit", "fred": "connection refused"}
        md = build_dashboard_metadata(
            "news_sentiment",
            engine_result={"confidence_score": 30, "signal_quality": "low",
                          "warnings": [], "missing_inputs": []},
            source_errors=se,
            source_freshness=sf,
            compute_duration_s=0.1,
        )
        # All headline fields should be degraded
        for comp in ("headline_sentiment", "negative_pressure", "narrative_severity",
                      "source_agreement", "recency_pressure"):
            assert md["field_status_map"][comp] == "degraded"
        assert md["field_status_map"]["macro_stress"] == "degraded"
        assert md["data_quality_status"] in ("poor", "unavailable")

    def test_news_error_payload_all_failed_source(self):
        md = build_dashboard_metadata(
            "news_sentiment",
            is_error_payload=True,
            error_stage="data_fetch",
        )
        for f, s in md["field_status_map"].items():
            assert s == "failed_source"
        assert md["data_quality_status"] == "unavailable"


# ════════════════════════════════════════════════════════════════════════
# 20. CROSS-ENGINE KNOWN-FIELD CONSISTENCY
# ════════════════════════════════════════════════════════════════════════

class TestCrossEngineFieldConsistency:
    """All engines have consistent known-field patterns."""

    def test_all_engines_have_common_fields(self):
        common = {"score", "label", "confidence_score", "signal_quality", "summary"}
        for engine_key in ENGINE_DASHBOARD_META:
            fields = set(_get_known_fields_for_engine(engine_key))
            assert common.issubset(fields), f"{engine_key} missing common fields: {common - fields}"

    def test_all_engines_have_engine_specific_fields(self):
        for engine_key in ENGINE_DASHBOARD_META:
            fields = _get_known_fields_for_engine(engine_key)
            # More than just common
            assert len(fields) > 5, f"{engine_key} has only common fields"

    def test_all_engine_field_status_maps_valid_vocabulary(self):
        """Every engine's field_status_map uses only valid vocabulary values."""
        for engine_key in ENGINE_DASHBOARD_META:
            md = build_dashboard_metadata(engine_key)
            for f, s in md["field_status_map"].items():
                assert s in FIELD_STATUS_VALUES, (
                    f"{engine_key}: field '{f}' has invalid status '{s}'"
                )

    def test_error_payload_consistent_across_engines(self):
        """Error payload path produces identical structure for all engines."""
        for engine_key in ENGINE_DASHBOARD_META:
            md = build_dashboard_metadata(engine_key, is_error_payload=True)
            assert md["data_quality_status"] == "unavailable"
            assert md["last_successful_update"] is None
            for f, s in md["field_status_map"].items():
                assert s == "failed_source", (
                    f"{engine_key}: expected 'failed_source' for '{f}', got '{s}'"
                )

    def test_metadata_examples_healthy(self):
        """Representative metadata: healthy engine."""
        md = build_dashboard_metadata(
            "breadth_participation",
            engine_result={
                "confidence_score": 90, "signal_quality": "high",
                "warnings": [], "missing_inputs": [],
                "as_of": "2026-03-15T14:00:00Z",
            },
            compute_duration_s=0.3,
        )
        assert md["data_quality_status"] == "good"
        assert md["coverage_level"] == "full"
        assert md["freshness_status"] == "live"
        assert md["proxy_reliance_level"] == "none"
        assert md["confidence_impact"]["is_actionable"] is True
        assert len(md["missing_fields"]) == 0
        assert md["last_successful_update"] == "2026-03-15T14:00:00Z"

    def test_metadata_examples_stale(self):
        """Representative metadata: stale data."""
        sp = {
            "stale_1": {"type": "direct", "source": "fred", "stale_warning": True},
            "stale_2": {"type": "direct", "source": "fred", "stale_warning": True},
            "stale_3": {"type": "direct", "source": "fred", "stale_warning": True},
        }
        md = build_dashboard_metadata(
            "cross_asset_macro",
            engine_result={
                "confidence_score": 65, "signal_quality": "moderate",
                "warnings": [], "missing_inputs": [],
                "diagnostics": {"signal_provenance": sp},
            },
            compute_duration_s=0.5,
        )
        assert md["freshness_status"] == "very_stale"
        assert len(md["stale_fields"]) == 3

    def test_metadata_examples_proxy(self):
        """Representative metadata: heavy proxy reliance."""
        sp = {f"proxy_{i}": {"type": "proxy", "source": "derived"} for i in range(8)}
        sp["direct_1"] = {"type": "direct", "source": "tradier"}
        md = build_dashboard_metadata(
            "flows_positioning",
            engine_result={
                "confidence_score": 55, "signal_quality": "moderate",
                "warnings": [], "missing_inputs": [],
                "diagnostics": {"signal_provenance": sp},
            },
        )
        assert md["proxy_reliance_level"] in ("high", "critical")
        assert len(md["proxy_fields"]) == 8

    def test_metadata_examples_failure(self):
        """Representative metadata: engine failure."""
        md = build_dashboard_metadata(
            "volatility_options",
            is_error_payload=True,
            error_stage="data_fetch",
        )
        assert md["data_quality_status"] == "unavailable"
        assert md["coverage_level"] == "none"
        assert md["last_successful_update"] is None
        assert any("data_fetch" in n for n in md["notes"])
