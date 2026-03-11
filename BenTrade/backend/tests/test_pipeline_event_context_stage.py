"""Tests for pipeline_event_context_stage — Step 10.

Covers:
  - Vocabulary/constant registration
  - Time window helpers
  - Relevance classification logic
  - Risk flag computation
  - Event normalization + split + nearest
  - Category context builders
  - Default event provider contract
  - Per-candidate event context builder
  - Handler contract (return shape, summary_counts, metadata)
  - Single-candidate processing
  - Multi-candidate processing
  - Vacuous completion (no enrichment summary, no candidates)
  - Degraded provider results
  - Provider failure handling
  - Partial candidate failures
  - Artifact creation and lineage
  - Stage summary structure
  - Event emission (started/completed/failed)
  - Orchestrator integration (wiring, deps, pipeline run)
"""

import pytest

from app.services.pipeline_event_context_stage import (
    _STAGE_KEY,
    _EVENT_CONTEXT_VERSION,
    NEARBY_DAYS,
    SOON_DAYS,
    EXTENDED_DAYS,
    EVENT_TYPE_EARNINGS,
    EVENT_TYPE_ECONOMIC,
    EVENT_TYPE_EXPIRY,
    EVENT_TYPE_DIVIDEND,
    EVENT_TYPE_HOLIDAY,
    VALID_EVENT_CATEGORY_TYPES,
    RELEVANCE_HIGH,
    RELEVANCE_MODERATE,
    RELEVANCE_LOW,
    RELEVANCE_NOT_RELEVANT,
    VALID_RELEVANCE_LEVELS,
    EVENT_STATUS_ENRICHED,
    EVENT_STATUS_ENRICHED_DEGRADED,
    EVENT_STATUS_NO_RELEVANT,
    EVENT_STATUS_FAILED,
    RISK_EARNINGS_NEARBY,
    RISK_MACRO_NEARBY,
    RISK_EXPIRY_NEARBY,
    RISK_WINDOW_OVERLAP,
    RISK_NO_COVERAGE,
    RISK_LOOKUP_DEGRADED,
    VALID_RISK_FLAGS,
    default_event_provider,
    days_between,
    is_within_window,
    classify_event_relevance,
    compute_risk_flags,
    normalize_event,
    split_upcoming_recent,
    find_nearest_relevant,
    build_category_context,
    build_candidate_event_context,
    event_context_handler,
)
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    VALID_EVENT_TYPES,
    create_pipeline_run,
    mark_stage_completed,
    mark_stage_running,
)
from app.services.pipeline_artifact_store import (
    VALID_ARTIFACT_TYPES,
    build_artifact_record,
    create_artifact_store,
    get_artifact_by_key,
    list_stage_artifacts,
    put_artifact,
)
from app.services.pipeline_orchestrator import (
    execute_stage,
    get_default_handlers,
    get_default_dependency_map,
    run_pipeline_with_handlers,
)


# =====================================================================
#  Test helpers
# =====================================================================

def _make_run_and_store(run_id="test-event-001"):
    """Create a fresh run+store with upstream stages completed."""
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    for stage in (
        "market_data", "market_model_analysis",
        "scanners", "candidate_selection",
        "shared_context", "candidate_enrichment",
    ):
        mark_stage_running(run, stage)
        mark_stage_completed(run, stage)
    return run, store


def _success_handler(run, artifact_store, stage_key, **kwargs):
    """Always-succeed stub handler."""
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 0},
        "artifacts": [],
        "metadata": {"stub": True},
        "error": None,
    }


def _all_stub_pipeline(**kwargs):
    """Run pipeline with all real-handler stages stubbed out."""
    handlers = kwargs.pop("handlers", {})
    handlers.setdefault("market_data", _success_handler)
    handlers.setdefault("market_model_analysis", _success_handler)
    handlers.setdefault("scanners", _success_handler)
    handlers.setdefault("candidate_selection", _success_handler)
    handlers.setdefault("shared_context", _success_handler)
    handlers.setdefault("candidate_enrichment", _success_handler)
    handlers.setdefault("events", _success_handler)
    handlers.setdefault("policy", _success_handler)
    handlers.setdefault("orchestration", _success_handler)
    handlers.setdefault("prompt_payload", _success_handler)
    handlers.setdefault("final_model_decision", _success_handler)
    handlers.setdefault("final_response_normalization", _success_handler)
    return run_pipeline_with_handlers(handlers, **kwargs)


def _write_enriched_candidate(
    store, run_id, candidate_id, symbol="SPY", **extra
):
    """Write a Step 9 enriched candidate artifact."""
    data = {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "symbol": symbol,
        "strategy_type": extra.get("strategy_type", "put_credit_spread"),
        "scanner_key": extra.get("scanner_key", "test_scanner"),
        "scanner_family": extra.get("scanner_family", "options"),
        "direction": extra.get("direction", "long"),
        "rank_position": extra.get("rank_position", 1),
        "rank_score": extra.get("rank_score", 0.85),
        "setup_quality": extra.get("setup_quality", 75.0),
        "confidence": extra.get("confidence", 0.8),
        "candidate_snapshot": {},
        "shared_context_artifact_ref": extra.get("shared_context_ref"),
        "compact_context_summary": {},
        "enrichment_status": "full",
        "enrichment_notes": [],
        "event_context": None,
        "portfolio_context": None,
        "policy_context": None,
        "decision_packet": None,
        "prompt_payload": None,
        "final_response": None,
        "enriched_at": "2026-03-11T00:00:00+00:00",
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="candidate_enrichment",
        artifact_key=f"enriched_{candidate_id}",
        artifact_type="enriched_candidate",
        data=data,
        candidate_id=candidate_id,
        summary={"candidate_id": candidate_id, "symbol": symbol},
    )
    put_artifact(store, art, overwrite=True)
    return data


def _write_enrichment_summary(store, run_id, candidate_ids):
    """Write a Step 9 enrichment summary artifact."""
    data = {
        "stage_key": "candidate_enrichment",
        "overall_status": "full",
        "total_candidates_in": len(candidate_ids),
        "total_enriched": len(candidate_ids),
        "total_full": len(candidate_ids),
        "total_degraded": 0,
        "total_failed": 0,
        "enrichment_records": [
            {
                "candidate_id": cid,
                "enrichment_status": "full",
                "enrichment_notes": [],
                "elapsed_ms": 1,
            }
            for cid in candidate_ids
        ],
        "enriched_artifact_refs": [],
        "shared_context_artifact_ref": None,
        "summary_artifact_ref": None,
        "elapsed_ms": 10,
        "generated_at": "2026-03-11T00:00:00+00:00",
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="candidate_enrichment",
        artifact_key="candidate_enrichment_summary",
        artifact_type="candidate_enrichment_summary",
        data=data,
        summary={"total_enriched": len(candidate_ids)},
    )
    put_artifact(store, art, overwrite=True)
    return data


def _populate_upstream(store, run_id, candidate_ids, symbols=None):
    """Write enriched candidates + summary (Step 9 output)."""
    symbols = symbols or {}
    for cid in candidate_ids:
        sym = symbols.get(cid, "SPY")
        _write_enriched_candidate(store, run_id, cid, symbol=sym)
    _write_enrichment_summary(store, run_id, candidate_ids)


def _make_event(
    event_type="earnings",
    event_name="Q1 Earnings",
    event_date="2026-03-15",
    source="mock",
    **metadata,
):
    """Create a raw event record."""
    return {
        "event_type": event_type,
        "event_name": event_name,
        "event_date": event_date,
        "source": source,
        "metadata": metadata,
    }


def _mock_provider(
    company_events=None,
    macro_events=None,
    expiry_events=None,
    status="available",
    degraded_reasons=None,
):
    """Return a mock event provider."""
    def provider(lookup_input):
        return {
            "provider_status": status,
            "company_events": company_events or [],
            "macro_events": macro_events or [],
            "expiry_events": expiry_events or [],
            "source_info": {"sources": ["mock"]},
            "degraded_reasons": degraded_reasons or [],
        }
    return provider


def _failing_provider(error_msg="Provider exploded"):
    """Return a provider that raises."""
    def provider(lookup_input):
        raise RuntimeError(error_msg)
    return provider


# =====================================================================
#  Test classes
# =====================================================================

class TestConstants:
    """Verify vocabularies and types are registered."""

    def test_stage_key(self):
        assert _STAGE_KEY == "events"
        assert "events" in PIPELINE_STAGES

    def test_event_context_version(self):
        assert _EVENT_CONTEXT_VERSION == "1.0"

    def test_event_types_registered(self):
        for et in ("event_context_started", "event_context_completed",
                    "event_context_failed"):
            assert et in VALID_EVENT_TYPES, f"{et} not in VALID_EVENT_TYPES"

    def test_artifact_types_registered(self):
        assert "event_context" in VALID_ARTIFACT_TYPES
        assert "event_context_summary" in VALID_ARTIFACT_TYPES

    def test_event_category_types(self):
        assert EVENT_TYPE_EARNINGS in VALID_EVENT_CATEGORY_TYPES
        assert EVENT_TYPE_ECONOMIC in VALID_EVENT_CATEGORY_TYPES
        assert EVENT_TYPE_EXPIRY in VALID_EVENT_CATEGORY_TYPES
        assert EVENT_TYPE_DIVIDEND in VALID_EVENT_CATEGORY_TYPES
        assert EVENT_TYPE_HOLIDAY in VALID_EVENT_CATEGORY_TYPES

    def test_relevance_levels(self):
        for level in (RELEVANCE_HIGH, RELEVANCE_MODERATE,
                      RELEVANCE_LOW, RELEVANCE_NOT_RELEVANT):
            assert level in VALID_RELEVANCE_LEVELS

    def test_risk_flags(self):
        for flag in (RISK_EARNINGS_NEARBY, RISK_MACRO_NEARBY,
                     RISK_EXPIRY_NEARBY, RISK_WINDOW_OVERLAP,
                     RISK_NO_COVERAGE, RISK_LOOKUP_DEGRADED):
            assert flag in VALID_RISK_FLAGS

    def test_time_thresholds(self):
        assert NEARBY_DAYS == 7
        assert SOON_DAYS == 14
        assert EXTENDED_DAYS == 30
        assert NEARBY_DAYS < SOON_DAYS < EXTENDED_DAYS


class TestTimeWindows:
    """Test days_between and is_within_window."""

    def test_days_between_future(self):
        assert days_between("2026-03-10", "2026-03-15") == 5

    def test_days_between_past(self):
        assert days_between("2026-03-10", "2026-03-05") == -5

    def test_days_between_same_day(self):
        assert days_between("2026-03-10", "2026-03-10") == 0

    def test_days_between_invalid_date(self):
        assert days_between("bad", "2026-03-10") is None
        assert days_between("2026-03-10", "bad") is None

    def test_days_between_none_input(self):
        assert days_between(None, "2026-03-10") is None

    def test_days_between_truncates_timestamp(self):
        assert days_between("2026-03-10T12:30:00Z", "2026-03-15T06:00:00Z") == 5

    def test_is_within_window_true(self):
        assert is_within_window(0, 7) is True
        assert is_within_window(5, 7) is True
        assert is_within_window(7, 7) is True

    def test_is_within_window_false(self):
        assert is_within_window(8, 7) is False
        assert is_within_window(-1, 7) is False

    def test_is_within_window_none(self):
        assert is_within_window(None, 7) is False


class TestRelevanceClassification:
    """Test classify_event_relevance."""

    # Earnings / economic follow same rules
    @pytest.mark.parametrize("event_type", [EVENT_TYPE_EARNINGS, EVENT_TYPE_ECONOMIC])
    def test_earnings_economic_nearby(self, event_type):
        assert classify_event_relevance(event_type, 0) == RELEVANCE_HIGH
        assert classify_event_relevance(event_type, 7) == RELEVANCE_HIGH

    @pytest.mark.parametrize("event_type", [EVENT_TYPE_EARNINGS, EVENT_TYPE_ECONOMIC])
    def test_earnings_economic_soon(self, event_type):
        assert classify_event_relevance(event_type, 8) == RELEVANCE_MODERATE
        assert classify_event_relevance(event_type, 14) == RELEVANCE_MODERATE

    @pytest.mark.parametrize("event_type", [EVENT_TYPE_EARNINGS, EVENT_TYPE_ECONOMIC])
    def test_earnings_economic_extended(self, event_type):
        assert classify_event_relevance(event_type, 15) == RELEVANCE_LOW
        assert classify_event_relevance(event_type, 30) == RELEVANCE_LOW

    @pytest.mark.parametrize("event_type", [EVENT_TYPE_EARNINGS, EVENT_TYPE_ECONOMIC])
    def test_earnings_economic_beyond(self, event_type):
        assert classify_event_relevance(event_type, 31) == RELEVANCE_NOT_RELEVANT
        assert classify_event_relevance(event_type, 90) == RELEVANCE_NOT_RELEVANT

    def test_expiry_nearby(self):
        assert classify_event_relevance(EVENT_TYPE_EXPIRY, 0) == RELEVANCE_HIGH
        assert classify_event_relevance(EVENT_TYPE_EXPIRY, 7) == RELEVANCE_HIGH

    def test_expiry_soon(self):
        assert classify_event_relevance(EVENT_TYPE_EXPIRY, 8) == RELEVANCE_MODERATE
        assert classify_event_relevance(EVENT_TYPE_EXPIRY, 14) == RELEVANCE_MODERATE

    def test_expiry_beyond(self):
        assert classify_event_relevance(EVENT_TYPE_EXPIRY, 15) == RELEVANCE_LOW
        assert classify_event_relevance(EVENT_TYPE_EXPIRY, 90) == RELEVANCE_LOW

    def test_other_types_nearby(self):
        assert classify_event_relevance(EVENT_TYPE_DIVIDEND, 5) == RELEVANCE_MODERATE
        assert classify_event_relevance(EVENT_TYPE_HOLIDAY, 3) == RELEVANCE_MODERATE

    def test_other_types_soon(self):
        assert classify_event_relevance(EVENT_TYPE_DIVIDEND, 10) == RELEVANCE_LOW

    def test_other_types_beyond(self):
        assert classify_event_relevance(EVENT_TYPE_DIVIDEND, 20) == RELEVANCE_NOT_RELEVANT

    def test_none_days(self):
        assert classify_event_relevance(EVENT_TYPE_EARNINGS, None) == RELEVANCE_LOW

    def test_negative_days_past_event(self):
        # Negative days (past events) use abs_days
        assert classify_event_relevance(EVENT_TYPE_EARNINGS, -3) == RELEVANCE_HIGH


class TestRiskFlags:
    """Test compute_risk_flags."""

    def test_no_events_available(self):
        flags = compute_risk_flags([], "available")
        assert flags == []

    def test_no_events_no_coverage(self):
        flags = compute_risk_flags([], "no_live_sources")
        assert RISK_NO_COVERAGE in flags

    def test_no_events_degraded(self):
        flags = compute_risk_flags([], "degraded")
        assert RISK_LOOKUP_DEGRADED in flags

    def test_earnings_nearby_flag(self):
        events = [
            {"event_type": EVENT_TYPE_EARNINGS, "days_until": 5,
             "relevance": RELEVANCE_HIGH},
        ]
        flags = compute_risk_flags(events, "available")
        assert RISK_EARNINGS_NEARBY in flags

    def test_earnings_too_far(self):
        events = [
            {"event_type": EVENT_TYPE_EARNINGS, "days_until": 20,
             "relevance": RELEVANCE_LOW},
        ]
        flags = compute_risk_flags(events, "available")
        assert RISK_EARNINGS_NEARBY not in flags

    def test_macro_nearby_flag(self):
        events = [
            {"event_type": EVENT_TYPE_ECONOMIC, "days_until": 3,
             "relevance": RELEVANCE_HIGH},
        ]
        flags = compute_risk_flags(events, "available")
        assert RISK_MACRO_NEARBY in flags

    def test_expiry_nearby_flag(self):
        events = [
            {"event_type": EVENT_TYPE_EXPIRY, "days_until": 5,
             "relevance": RELEVANCE_HIGH},
        ]
        flags = compute_risk_flags(events, "available")
        assert RISK_EXPIRY_NEARBY in flags

    def test_expiry_not_nearby(self):
        events = [
            {"event_type": EVENT_TYPE_EXPIRY, "days_until": 10,
             "relevance": RELEVANCE_MODERATE},
        ]
        flags = compute_risk_flags(events, "available")
        assert RISK_EXPIRY_NEARBY not in flags

    def test_window_overlap(self):
        events = [
            {"event_type": EVENT_TYPE_EARNINGS, "days_until": 3,
             "relevance": RELEVANCE_HIGH},
            {"event_type": EVENT_TYPE_ECONOMIC, "days_until": 5,
             "relevance": RELEVANCE_HIGH},
        ]
        flags = compute_risk_flags(events, "available")
        assert RISK_WINDOW_OVERLAP in flags
        assert RISK_EARNINGS_NEARBY in flags
        assert RISK_MACRO_NEARBY in flags

    def test_no_overlap_one_high(self):
        events = [
            {"event_type": EVENT_TYPE_EARNINGS, "days_until": 3,
             "relevance": RELEVANCE_HIGH},
            {"event_type": EVENT_TYPE_DIVIDEND, "days_until": 10,
             "relevance": RELEVANCE_LOW},
        ]
        flags = compute_risk_flags(events, "available")
        assert RISK_WINDOW_OVERLAP not in flags

    def test_no_duplicate_flags(self):
        events = [
            {"event_type": EVENT_TYPE_EARNINGS, "days_until": 3,
             "relevance": RELEVANCE_HIGH},
            {"event_type": EVENT_TYPE_EARNINGS, "days_until": 7,
             "relevance": RELEVANCE_HIGH},
        ]
        flags = compute_risk_flags(events, "available")
        assert flags.count(RISK_EARNINGS_NEARBY) == 1

    def test_past_events_no_flags(self):
        # days_until < 0 should not trigger flags (gate is 0 <= days)
        events = [
            {"event_type": EVENT_TYPE_EARNINGS, "days_until": -3,
             "relevance": RELEVANCE_HIGH},
        ]
        flags = compute_risk_flags(events, "available")
        assert RISK_EARNINGS_NEARBY not in flags


class TestEventNormalization:
    """Test normalize_event, split_upcoming_recent, find_nearest_relevant."""

    def test_normalize_event_basic(self):
        raw = _make_event(event_type="earnings", event_date="2026-03-15")
        result = normalize_event(raw, "2026-03-10")
        assert result["event_type"] == "earnings"
        assert result["days_until"] == 5
        assert result["relevance"] == RELEVANCE_HIGH
        assert result["source"] == "mock"

    def test_normalize_no_event_date(self):
        raw = {"event_type": "earnings", "event_name": "Unknown", "source": "x"}
        result = normalize_event(raw, "2026-03-10")
        assert result["days_until"] is None
        assert result["relevance"] == RELEVANCE_LOW

    def test_normalize_defaults(self):
        raw = {}
        result = normalize_event(raw, "2026-03-10")
        assert result["event_type"] == "unknown"
        assert result["event_name"] == ""
        assert result["source"] == "unknown"
        assert result["metadata"] == {}

    def test_split_upcoming_recent(self):
        events = [
            {"days_until": 5},
            {"days_until": -3},
            {"days_until": 0},
            {"days_until": None},
        ]
        upcoming, recent = split_upcoming_recent(events)
        assert len(upcoming) == 3  # 5, 0, None
        assert len(recent) == 1    # -3

    def test_split_empty(self):
        upcoming, recent = split_upcoming_recent([])
        assert upcoming == []
        assert recent == []

    def test_find_nearest_relevant(self):
        events = [
            {"days_until": 10, "relevance": RELEVANCE_MODERATE},
            {"days_until": 3, "relevance": RELEVANCE_HIGH},
            {"days_until": 20, "relevance": RELEVANCE_LOW},
        ]
        nearest = find_nearest_relevant(events)
        assert nearest["days_until"] == 3

    def test_find_nearest_skips_not_relevant(self):
        events = [
            {"days_until": 3, "relevance": RELEVANCE_NOT_RELEVANT},
            {"days_until": 10, "relevance": RELEVANCE_MODERATE},
        ]
        nearest = find_nearest_relevant(events)
        assert nearest["days_until"] == 10

    def test_find_nearest_none_if_empty(self):
        assert find_nearest_relevant([]) is None

    def test_find_nearest_none_if_all_not_relevant(self):
        events = [
            {"days_until": 5, "relevance": RELEVANCE_NOT_RELEVANT},
        ]
        assert find_nearest_relevant(events) is None

    def test_find_nearest_skips_none_days(self):
        events = [
            {"days_until": None, "relevance": RELEVANCE_HIGH},
            {"days_until": 8, "relevance": RELEVANCE_MODERATE},
        ]
        nearest = find_nearest_relevant(events)
        assert nearest["days_until"] == 8


class TestCategoryContext:
    """Test build_category_context."""

    def test_category_with_events(self):
        events = [
            {"event_type": "earnings", "days_until": 5, "relevance": "high"},
            {"event_type": "economic", "days_until": 3, "relevance": "high"},
            {"event_type": "earnings", "days_until": 20, "relevance": "low"},
        ]
        ctx = build_category_context(events, "earnings")
        assert ctx["available"] is True
        assert ctx["event_count"] == 2
        assert len(ctx["events"]) == 2
        assert ctx["nearest"]["days_until"] == 5
        assert ctx["has_nearby"] is True

    def test_category_no_match(self):
        events = [
            {"event_type": "economic", "days_until": 3, "relevance": "high"},
        ]
        ctx = build_category_context(events, "earnings")
        assert ctx["available"] is False
        assert ctx["event_count"] == 0
        assert ctx["nearest"] is None
        assert ctx["has_nearby"] is False

    def test_category_empty_events(self):
        ctx = build_category_context([], "earnings")
        assert ctx["available"] is False
        assert ctx["event_count"] == 0


class TestDefaultProvider:
    """Test default_event_provider."""

    def test_returns_no_live_sources(self):
        result = default_event_provider({"symbol": "SPY"})
        assert result["provider_status"] == "no_live_sources"
        assert result["company_events"] == []
        assert result["macro_events"] == []
        assert result["expiry_events"] == []
        assert len(result["degraded_reasons"]) > 0

    def test_source_info(self):
        result = default_event_provider({"symbol": "QQQ"})
        assert "sources" in result["source_info"]
        assert result["source_info"]["sources"] == []


class TestCandidateEventContext:
    """Test build_candidate_event_context."""

    def test_basic_with_events(self):
        enriched = {"candidate_id": "c1", "symbol": "SPY"}
        provider_result = {
            "provider_status": "available",
            "company_events": [
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
            "macro_events": [
                _make_event(event_type="economic", event_name="FOMC",
                            event_date="2026-03-12"),
            ],
            "expiry_events": [],
            "source_info": {"sources": ["mock"]},
            "degraded_reasons": [],
        }
        ctx = build_candidate_event_context(
            enriched, "art-001", provider_result, "run-1", "2026-03-10",
        )
        assert ctx["event_context_version"] == _EVENT_CONTEXT_VERSION
        assert ctx["candidate_id"] == "c1"
        assert ctx["symbol"] == "SPY"
        assert ctx["source_enriched_candidate_ref"] == "art-001"
        assert ctx["event_status"] == EVENT_STATUS_ENRICHED
        assert ctx["downstream_usable"] is True
        assert len(ctx["upcoming_events"]) == 2
        assert ctx["nearest_relevant_event"] is not None
        assert ctx["event_summary"]["total_events"] == 2
        assert ctx["company_event_context"]["available"] is True
        assert ctx["macro_event_context"]["available"] is True
        assert ctx["expiry_event_context"]["available"] is False
        assert RISK_EARNINGS_NEARBY in ctx["event_risk_flags"]
        assert RISK_MACRO_NEARBY in ctx["event_risk_flags"]

    def test_no_events_available_provider(self):
        enriched = {"candidate_id": "c2", "symbol": "QQQ"}
        provider_result = {
            "provider_status": "available",
            "company_events": [],
            "macro_events": [],
            "expiry_events": [],
            "source_info": {"sources": ["mock"]},
            "degraded_reasons": [],
        }
        ctx = build_candidate_event_context(
            enriched, "art-002", provider_result, "run-1", "2026-03-10",
        )
        assert ctx["event_status"] == EVENT_STATUS_NO_RELEVANT
        assert ctx["downstream_usable"] is True
        assert ctx["event_summary"]["total_events"] == 0
        assert ctx["nearest_relevant_event"] is None

    def test_no_live_sources(self):
        enriched = {"candidate_id": "c3", "symbol": "IWM"}
        provider_result = default_event_provider({"symbol": "IWM"})
        ctx = build_candidate_event_context(
            enriched, "art-003", provider_result, "run-1", "2026-03-10",
        )
        assert ctx["event_status"] == EVENT_STATUS_NO_RELEVANT
        assert ctx["downstream_usable"] is True
        assert RISK_NO_COVERAGE in ctx["event_risk_flags"]

    def test_degraded_with_events(self):
        enriched = {"candidate_id": "c4", "symbol": "DIA"}
        provider_result = {
            "provider_status": "degraded",
            "company_events": [
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
            "macro_events": [],
            "expiry_events": [],
            "source_info": {"sources": ["partial"]},
            "degraded_reasons": ["earnings calendar stale"],
        }
        ctx = build_candidate_event_context(
            enriched, "art-004", provider_result, "run-1", "2026-03-10",
        )
        assert ctx["event_status"] == EVENT_STATUS_ENRICHED_DEGRADED
        assert ctx["downstream_usable"] is True
        assert RISK_LOOKUP_DEGRADED in ctx["event_risk_flags"]

    def test_degraded_no_events(self):
        enriched = {"candidate_id": "c5", "symbol": "XSP"}
        provider_result = {
            "provider_status": "degraded",
            "company_events": [],
            "macro_events": [],
            "expiry_events": [],
            "source_info": {"sources": ["partial"]},
            "degraded_reasons": ["API unavailable"],
        }
        ctx = build_candidate_event_context(
            enriched, "art-005", provider_result, "run-1", "2026-03-10",
        )
        assert ctx["event_status"] == EVENT_STATUS_ENRICHED_DEGRADED
        assert ctx["downstream_usable"] is True

    def test_failed_provider(self):
        enriched = {"candidate_id": "c6", "symbol": "RUT"}
        provider_result = {
            "provider_status": "failed",
            "company_events": [],
            "macro_events": [],
            "expiry_events": [],
            "source_info": {},
            "degraded_reasons": ["total failure"],
        }
        ctx = build_candidate_event_context(
            enriched, "art-006", provider_result, "run-1", "2026-03-10",
        )
        assert ctx["event_status"] == EVENT_STATUS_FAILED
        assert ctx["downstream_usable"] is False

    def test_recent_events_separated(self):
        enriched = {"candidate_id": "c7", "symbol": "SPY"}
        provider_result = {
            "provider_status": "available",
            "company_events": [
                _make_event(event_type="earnings", event_date="2026-03-05"),
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
            "macro_events": [],
            "expiry_events": [],
            "source_info": {"sources": ["mock"]},
            "degraded_reasons": [],
        }
        ctx = build_candidate_event_context(
            enriched, "art-007", provider_result, "run-1", "2026-03-10",
        )
        assert len(ctx["recent_events"]) == 1
        assert len(ctx["upcoming_events"]) == 1
        assert ctx["recent_events"][0]["days_until"] == -5
        assert ctx["upcoming_events"][0]["days_until"] == 5

    def test_contract_fields_present(self):
        enriched = {"candidate_id": "c8", "symbol": "NDX"}
        provider_result = default_event_provider({"symbol": "NDX"})
        ctx = build_candidate_event_context(
            enriched, "art-008", provider_result, "run-1", "2026-03-10",
        )
        required_keys = {
            "event_context_version", "run_id", "candidate_id",
            "source_enriched_candidate_ref", "symbol", "event_status",
            "event_summary", "upcoming_events", "recent_events",
            "nearest_relevant_event", "macro_event_context",
            "company_event_context", "expiry_event_context",
            "event_risk_flags", "event_source_refs",
            "degraded_reasons", "downstream_usable", "generated_at",
        }
        assert required_keys.issubset(ctx.keys())


class TestHandlerContract:
    """Test handler return shape matches orchestrator expectations."""

    def test_return_shape_required_keys(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "events")
        result = event_context_handler(run, store, "events")
        assert "outcome" in result
        assert "summary_counts" in result
        assert "artifacts" in result
        assert "metadata" in result
        assert "error" in result

    def test_outcome_completed_no_upstream(self):
        """Without enrichment summary, handler completes vacuously."""
        run, store = _make_run_and_store()
        mark_stage_running(run, "events")
        result = event_context_handler(run, store, "events")
        assert result["outcome"] == "completed"
        assert result["error"] is None

    def test_summary_counts_keys(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "events")
        result = event_context_handler(run, store, "events")
        sc = result["summary_counts"]
        for key in ("total_processed", "total_enriched", "total_no_events",
                     "total_degraded", "total_failed"):
            assert key in sc

    def test_metadata_has_stage_status(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "events")
        result = event_context_handler(run, store, "events")
        assert "stage_status" in result["metadata"]


class TestSingleCandidate:
    """Test processing a single candidate with events."""

    def test_single_candidate_with_earnings(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-1"])
        mark_stage_running(run, "events")

        provider = _mock_provider(
            company_events=[
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
        )
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_enriched"] == 1

        # Verify artifact written
        art = get_artifact_by_key(store, "events", "event_cand-1")
        assert art is not None
        assert art["data"]["event_status"] == EVENT_STATUS_ENRICHED
        assert art["data"]["candidate_id"] == "cand-1"

    def test_single_candidate_no_events(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-2"])
        mark_stage_running(run, "events")

        provider = _mock_provider()  # empty events
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_no_events"] == 1

    def test_single_candidate_default_provider(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-3"])
        mark_stage_running(run, "events")

        # Uses default_event_provider (no live sources)
        result = event_context_handler(run, store, "events")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_no_events"] == 1


class TestMultipleCandidates:
    """Test processing multiple candidates."""

    def test_multiple_candidates(self):
        run, store = _make_run_and_store()
        cid_list = ["cand-a", "cand-b", "cand-c"]
        _populate_upstream(
            store, run["run_id"], cid_list,
            symbols={"cand-a": "SPY", "cand-b": "QQQ", "cand-c": "IWM"},
        )
        mark_stage_running(run, "events")

        provider = _mock_provider(
            company_events=[
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
        )
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_enriched"] == 3

        # Each candidate has its own artifact
        for cid in cid_list:
            art = get_artifact_by_key(store, "events", f"event_{cid}")
            assert art is not None
            assert art["data"]["candidate_id"] == cid


class TestVacuousCompletion:
    """Test vacuous completion paths."""

    def test_no_enrichment_summary(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "events")
        result = event_context_handler(run, store, "events")
        assert result["outcome"] == "completed"
        assert result["metadata"]["stage_status"] == "no_candidates_to_process"

        # Summary artifact still written
        art = get_artifact_by_key(store, "events", "event_context_summary")
        assert art is not None
        assert art["data"]["stage_status"] == "no_candidates_to_process"

    def test_empty_enrichment_records(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], [])
        mark_stage_running(run, "events")
        result = event_context_handler(run, store, "events")
        assert result["outcome"] == "completed"
        assert result["metadata"]["stage_status"] == "no_candidates_to_process"


class TestDegradedProvider:
    """Test degraded provider results."""

    def test_degraded_with_events(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-deg"])
        mark_stage_running(run, "events")

        provider = _mock_provider(
            company_events=[
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
            status="degraded",
            degraded_reasons=["earnings calendar stale"],
        )
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_degraded"] == 1
        assert result["metadata"]["stage_status"] == "degraded"

    def test_degraded_no_events(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-deg2"])
        mark_stage_running(run, "events")

        provider = _mock_provider(status="degraded", degraded_reasons=["API down"])
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_degraded"] == 1


class TestProviderFailure:
    """Test provider that raises exceptions."""

    def test_provider_exception_single_candidate(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-fail"])
        mark_stage_running(run, "events")

        provider = _failing_provider("API crash")
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["total_failed"] == 1
        assert result["error"] is not None
        assert result["error"]["code"] == "EVENT_CONTEXT_ALL_FAILED"

    def test_provider_exception_partial(self):
        """One candidate fails, another succeeds."""
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-ok", "cand-boom"],
            symbols={"cand-ok": "SPY", "cand-boom": "QQQ"},
        )
        mark_stage_running(run, "events")

        call_count = {"n": 0}

        def mixed_provider(lookup_input):
            call_count["n"] += 1
            if lookup_input["symbol"] == "QQQ":
                raise RuntimeError("QQQ provider error")
            return {
                "provider_status": "available",
                "company_events": [],
                "macro_events": [],
                "expiry_events": [],
                "source_info": {"sources": ["mock"]},
                "degraded_reasons": [],
            }

        result = event_context_handler(
            run, store, "events",
            event_provider=mixed_provider,
        )
        # One succeeded, one failed → degraded (not total failure)
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_no_events"] == 1
        assert result["summary_counts"]["total_failed"] == 1
        assert result["metadata"]["stage_status"] == "degraded"


class TestPartialFailures:
    """Test partial candidate failures."""

    def test_enriched_packet_missing(self):
        """Enrichment summary lists a candidate whose artifact is missing."""
        run, store = _make_run_and_store()
        # Write summary with 2 candidates, but only 1 actual enriched artifact
        _write_enrichment_summary(store, run["run_id"], ["cand-ok", "cand-ghost"])
        _write_enriched_candidate(store, run["run_id"], "cand-ok")
        mark_stage_running(run, "events")

        provider = _mock_provider()
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_failed"] == 1
        assert result["summary_counts"]["total_no_events"] == 1
        assert result["metadata"]["stage_status"] == "degraded"

    def test_all_candidates_missing(self):
        """Enrichment summary has candidates but no enriched packets."""
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], ["ghost-1", "ghost-2"])
        mark_stage_running(run, "events")

        result = event_context_handler(run, store, "events")
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["total_failed"] == 2


class TestArtifactWriting:
    """Test artifact creation and lineage."""

    def test_per_candidate_artifact_keyed(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-art"])
        mark_stage_running(run, "events")

        provider = _mock_provider(
            company_events=[
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
        )
        event_context_handler(
            run, store, "events",
            event_provider=provider,
        )

        art = get_artifact_by_key(store, "events", "event_cand-art")
        assert art is not None
        assert art["artifact_type"] == "event_context"
        assert art["stage_key"] == "events"
        assert art["candidate_id"] == "cand-art"

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-sum"])
        mark_stage_running(run, "events")

        event_context_handler(run, store, "events")

        art = get_artifact_by_key(store, "events", "event_context_summary")
        assert art is not None
        assert art["artifact_type"] == "event_context_summary"
        assert art["data"]["stage_key"] == "events"

    def test_summary_artifact_on_vacuous(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "events")

        event_context_handler(run, store, "events")

        art = get_artifact_by_key(store, "events", "event_context_summary")
        assert art is not None
        assert art["data"]["total_candidates_in"] == 0

    def test_artifact_refs_in_metadata(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-ref1", "cand-ref2"])
        mark_stage_running(run, "events")

        result = event_context_handler(run, store, "events")
        refs = result["metadata"].get("output_artifact_refs", {})
        assert "cand-ref1" in refs
        assert "cand-ref2" in refs


class TestStageSummary:
    """Test stage summary structure."""

    def test_summary_fields(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-s1"])
        mark_stage_running(run, "events")

        provider = _mock_provider(
            company_events=[
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
        )
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        summary = result["metadata"]["stage_summary"]

        assert summary["stage_key"] == "events"
        assert summary["total_candidates_in"] == 1
        assert summary["total_enriched"] == 1
        assert "execution_records" in summary
        assert "risk_flag_counts" in summary
        assert "elapsed_ms" in summary
        assert "generated_at" in summary
        assert summary["summary_artifact_ref"] is not None

    def test_risk_flag_counts(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-rf1", "cand-rf2"],
            symbols={"cand-rf1": "SPY", "cand-rf2": "QQQ"},
        )
        mark_stage_running(run, "events")

        provider = _mock_provider(
            company_events=[
                _make_event(event_type="earnings", event_date="2026-03-15"),
            ],
        )
        result = event_context_handler(
            run, store, "events",
            event_provider=provider,
        )
        rfc = result["metadata"]["risk_flag_counts"]
        assert rfc.get(RISK_EARNINGS_NEARBY, 0) == 2

    def test_execution_records_per_candidate(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-er1", "cand-er2"])
        mark_stage_running(run, "events")

        result = event_context_handler(run, store, "events")
        summary = result["metadata"]["stage_summary"]
        records = summary["execution_records"]
        assert len(records) == 2
        cids = {r["candidate_id"] for r in records}
        assert cids == {"cand-er1", "cand-er2"}
        for r in records:
            assert "elapsed_ms" in r
            assert "event_status" in r


class TestEventEmission:
    """Test structured event callbacks."""

    def test_started_and_completed_emitted(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-ev1"])
        mark_stage_running(run, "events")

        events_received = []
        result = event_context_handler(
            run, store, "events",
            event_callback=events_received.append,
        )
        types = [e["event_type"] for e in events_received]
        assert "event_context_started" in types
        assert "event_context_completed" in types

    def test_failed_emitted_on_failure(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-evf"])
        mark_stage_running(run, "events")

        events_received = []
        result = event_context_handler(
            run, store, "events",
            event_callback=events_received.append,
            event_provider=_failing_provider(),
        )
        types = [e["event_type"] for e in events_received]
        assert "event_context_started" in types
        assert "event_context_failed" in types

    def test_vacuous_emits_completed(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "events")

        events_received = []
        result = event_context_handler(
            run, store, "events",
            event_callback=events_received.append,
        )
        types = [e["event_type"] for e in events_received]
        assert "event_context_started" in types
        assert "event_context_completed" in types

    def test_callback_exception_does_not_crash(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "events")

        def bad_callback(event):
            raise ValueError("callback exploded")

        result = event_context_handler(
            run, store, "events",
            event_callback=bad_callback,
        )
        assert result["outcome"] == "completed"


class TestInjectableProvider:
    """Test that event_provider kwarg is properly injectable."""

    def test_custom_provider_receives_lookup_input(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-inj"])
        mark_stage_running(run, "events")

        received_inputs = []

        def spy_provider(lookup_input):
            received_inputs.append(lookup_input)
            return default_event_provider(lookup_input)

        event_context_handler(
            run, store, "events",
            event_provider=spy_provider,
        )
        assert len(received_inputs) == 1
        li = received_inputs[0]
        assert li["symbol"] == "SPY"
        assert "strategy_type" in li
        assert "scanner_family" in li
        assert "direction" in li
        assert "as_of_date" in li
        assert "candidate_metadata" in li

    def test_provider_per_candidate(self):
        """Each candidate gets its own provider call."""
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["cand-p1", "cand-p2"],
            symbols={"cand-p1": "SPY", "cand-p2": "QQQ"},
        )
        mark_stage_running(run, "events")

        symbols_seen = []

        def tracking_provider(lookup_input):
            symbols_seen.append(lookup_input["symbol"])
            return default_event_provider(lookup_input)

        event_context_handler(
            run, store, "events",
            event_provider=tracking_provider,
        )
        assert set(symbols_seen) == {"SPY", "QQQ"}


class TestThresholdLogic:
    """Test time window and threshold boundary cases."""

    def test_earnings_exactly_at_boundary_nearby(self):
        enriched = {"candidate_id": "c-b1", "symbol": "SPY"}
        # Earnings exactly at NEARBY_DAYS (7 days out)
        pr = {
            "provider_status": "available",
            "company_events": [
                _make_event(event_type="earnings", event_date="2026-03-17"),
            ],
            "macro_events": [], "expiry_events": [],
            "source_info": {"sources": ["mock"]}, "degraded_reasons": [],
        }
        ctx = build_candidate_event_context(
            enriched, "art-b1", pr, "run-1", "2026-03-10",
        )
        assert ctx["upcoming_events"][0]["relevance"] == RELEVANCE_HIGH

    def test_earnings_one_past_nearby(self):
        enriched = {"candidate_id": "c-b2", "symbol": "SPY"}
        # Earnings at NEARBY_DAYS + 1 (8 days out)
        pr = {
            "provider_status": "available",
            "company_events": [
                _make_event(event_type="earnings", event_date="2026-03-18"),
            ],
            "macro_events": [], "expiry_events": [],
            "source_info": {"sources": ["mock"]}, "degraded_reasons": [],
        }
        ctx = build_candidate_event_context(
            enriched, "art-b2", pr, "run-1", "2026-03-10",
        )
        assert ctx["upcoming_events"][0]["relevance"] == RELEVANCE_MODERATE

    def test_economic_at_extended_boundary(self):
        enriched = {"candidate_id": "c-b3", "symbol": "SPY"}
        # Economic at exactly EXTENDED_DAYS (30 days out)
        pr = {
            "provider_status": "available",
            "company_events": [],
            "macro_events": [
                _make_event(event_type="economic", event_date="2026-04-09",
                            event_name="FOMC"),
            ],
            "expiry_events": [],
            "source_info": {"sources": ["mock"]}, "degraded_reasons": [],
        }
        ctx = build_candidate_event_context(
            enriched, "art-b3", pr, "run-1", "2026-03-10",
        )
        assert ctx["upcoming_events"][0]["relevance"] == RELEVANCE_LOW

    def test_economic_past_extended(self):
        enriched = {"candidate_id": "c-b4", "symbol": "SPY"}
        # Economic at EXTENDED_DAYS + 1 (31 days out)
        pr = {
            "provider_status": "available",
            "company_events": [],
            "macro_events": [
                _make_event(event_type="economic", event_date="2026-04-10",
                            event_name="CPI"),
            ],
            "expiry_events": [],
            "source_info": {"sources": ["mock"]}, "degraded_reasons": [],
        }
        ctx = build_candidate_event_context(
            enriched, "art-b4", pr, "run-1", "2026-03-10",
        )
        assert ctx["upcoming_events"][0]["relevance"] == RELEVANCE_NOT_RELEVANT


class TestOrchestratorIntegration:
    """Test wiring, dependencies, and full pipeline integration."""

    def test_default_handler_wired(self):
        handlers = get_default_handlers()
        assert handlers["events"] is event_context_handler

    def test_dependency_on_candidate_enrichment(self):
        deps = get_default_dependency_map()
        assert "candidate_enrichment" in deps["events"]

    def test_runs_through_pipeline_with_stubs(self):
        result = _all_stub_pipeline()
        run = result["run"]
        assert run["status"] in ("completed", "partial_failed")

    def test_execute_stage_with_handler(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["cand-orch"])

        result = execute_stage(
            run, store, _STAGE_KEY,
            handler=event_context_handler,
        )
        assert result["outcome"] == "completed"
        assert result["artifact_count"] == 0  # handler writes directly

    def test_full_pipeline_with_event_context(self):
        result = run_pipeline_with_handlers(
            {
                "market_data": _success_handler,
                "market_model_analysis": _success_handler,
                "scanners": _success_handler,
                "candidate_selection": _success_handler,
                "shared_context": _success_handler,
                "candidate_enrichment": _success_handler,
                "events": event_context_handler,
                "policy": _success_handler,
                "orchestration": _success_handler,
                "prompt_payload": _success_handler,
                "final_model_decision": _success_handler,
                "final_response_normalization": _success_handler,
            },
        )
        sr = {s["stage_key"]: s for s in result["stage_results"]}
        assert sr["events"]["outcome"] == "completed"

    def test_events_continuable(self):
        """Events stage failure should not halt the pipeline."""
        from app.services.pipeline_orchestrator import _CONTINUABLE_STAGES
        assert "events" in _CONTINUABLE_STAGES
