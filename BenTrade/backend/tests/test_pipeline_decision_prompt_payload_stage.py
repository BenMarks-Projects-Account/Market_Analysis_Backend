"""Tests for pipeline_decision_prompt_payload_stage — Step 13.

Covers:
  - Vocabulary/constant registration
  - Candidate section compression
  - Event section compression
  - Policy section compression (guardrail preservation)
  - Quality section compression
  - Prompt payload assembly
  - Rendered prompt text generation
  - Handler contract (return shape, summary_counts, metadata)
  - Single-candidate processing
  - Multi-candidate processing
  - Blocked candidate still produces valid payload
  - Restricted candidate still produces valid payload
  - Degraded event section compresses honestly
  - Quality section propagates into payload
  - Policy guardrails remain explicit
  - Partial candidate failures
  - Missing decision packet summary
  - Empty candidate set
  - Missing per-candidate decision packets
  - Artifact creation and lineage
  - Stage summary contents
  - Compression metadata
  - Event emission (started/completed/failed)
  - Orchestrator integration (wiring, deps, pipeline run)
  - Forward compatibility for final model execution stage
"""

import pytest

from app.services.pipeline_decision_prompt_payload_stage import (
    _STAGE_KEY,
    _PROMPT_PAYLOAD_VERSION,
    PAYLOAD_BUILT,
    PAYLOAD_BUILT_DEGRADED,
    PAYLOAD_FAILED,
    VALID_PAYLOAD_STATUSES,
    compress_candidate_section,
    compress_event_section,
    compress_policy_section,
    compress_quality_section,
    build_prompt_payload,
    render_prompt_text,
    prompt_payload_handler,
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

def _make_run_and_store(run_id="test-pp-001"):
    """Create a fresh run+store with upstream stages completed.

    Completes through orchestration (since orchestration runs before
    prompt_payload in the canonical stage order).
    """
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    for stage in (
        "market_data", "market_model_analysis",
        "scanners", "candidate_selection",
        "shared_context", "candidate_enrichment",
        "events", "policy", "orchestration",
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


# ── Decision packet builders (Step 12 output shapes) ────────────

def _make_candidate_section(
    candidate_id="c1",
    symbol="SPY",
    **overrides,
):
    """Build a minimal candidate section as Step 12 produces."""
    base = {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "scanner_key": overrides.get("scanner_key", "test_scanner"),
        "scanner_family": overrides.get("scanner_family", "options"),
        "strategy_type": overrides.get("strategy_type", "put_credit_spread"),
        "direction": overrides.get("direction", "long"),
        "rank_position": overrides.get("rank_position", 1),
        "rank_score": overrides.get("rank_score", 0.85),
        "setup_quality": overrides.get("setup_quality", 75.0),
        "confidence": overrides.get("confidence", 0.8),
        "enrichment_status": overrides.get("enrichment_status", "full"),
        "enrichment_notes": overrides.get("enrichment_notes", []),
        "compact_context_summary": overrides.get(
            "compact_context_summary", {"market_regime": "bullish"},
        ),
    }
    return base


def _make_event_section(available=True, **overrides):
    """Build a minimal event section as Step 12 produces."""
    if not available:
        return {
            "event_data_available": False,
            "event_status": None,
            "event_summary": {},
            "risk_flags": [],
            "nearest_event_type": None,
            "nearest_days_until": None,
            "degraded_reasons": ["event context not available"],
        }
    return {
        "event_data_available": True,
        "event_status": overrides.get("event_status", "enriched"),
        "event_summary": overrides.get("event_summary", {
            "nearest_event_type": "earnings",
            "nearest_days_until": 9,
            "total_events": 2,
        }),
        "risk_flags": overrides.get("risk_flags", []),
        "nearest_event_type": overrides.get(
            "nearest_event_type", "earnings",
        ),
        "nearest_days_until": overrides.get("nearest_days_until", 9),
        "degraded_reasons": overrides.get("degraded_reasons", []),
    }


def _make_policy_section(
    overall_outcome="eligible",
    **overrides,
):
    """Build a minimal policy section as Step 12 produces."""
    return {
        "policy_version": overrides.get("policy_version", "1.0"),
        "policy_status": overrides.get("policy_status", "evaluated"),
        "overall_outcome": overall_outcome,
        "checks": overrides.get("checks", [
            {"check_name": "capital_limits", "passed": True},
            {"check_name": "position_limits", "passed": True},
        ]),
        "blocking_reasons": overrides.get("blocking_reasons", []),
        "caution_reasons": overrides.get("caution_reasons", []),
        "restriction_reasons": overrides.get("restriction_reasons", []),
        "eligibility_flags": overrides.get("eligibility_flags", {
            "trade_capable": True,
        }),
        "portfolio_context_summary": overrides.get(
            "portfolio_context_summary", {"snapshot_status": "available"},
        ),
        "event_risk_summary": overrides.get(
            "event_risk_summary", {"event_data_available": True},
        ),
        "downstream_usable": overrides.get("downstream_usable", True),
        "degraded_reasons": overrides.get("degraded_reasons", []),
        "policy_metadata": overrides.get("policy_metadata", {}),
    }


def _make_quality_section(
    candidate_status="present",
    event_status="present",
    policy_status="present",
    downstream_usable=True,
    **overrides,
):
    """Build a minimal quality section as Step 12 produces."""
    statuses = {
        "candidate_section": candidate_status,
        "event_section": event_status,
        "policy_section": policy_status,
    }
    missing = [k for k, v in statuses.items() if v == "missing"]
    degraded = [k for k, v in statuses.items() if v == "degraded"]
    return {
        "section_statuses": statuses,
        "missing_sections": overrides.get("missing_sections", missing),
        "degraded_sections": overrides.get("degraded_sections", degraded),
        "degraded_reasons": overrides.get("degraded_reasons", []),
        "downstream_usable": downstream_usable,
    }


def _make_decision_packet(
    candidate_id="c1",
    symbol="SPY",
    packet_status="assembled",
    *,
    candidate_section=None,
    event_section=None,
    policy_section=None,
    quality_section=None,
    **overrides,
):
    """Build a minimal decision packet as Step 12 produces."""
    return {
        "decision_packet_version": "1.0",
        "run_id": overrides.get("run_id", "test-pp-001"),
        "candidate_id": candidate_id,
        "symbol": symbol,
        "packet_status": packet_status,
        "source_refs": overrides.get("source_refs", {
            "enriched_candidate_ref": "art-enr-001",
            "policy_ref": "art-pol-001",
            "event_context_ref": "art-evt-001",
        }),
        "candidate_section": candidate_section or _make_candidate_section(
            candidate_id, symbol,
        ),
        "event_section": event_section or _make_event_section(),
        "policy_section": policy_section or _make_policy_section(),
        "quality_section": quality_section or _make_quality_section(),
        "metadata": overrides.get("metadata", {
            "assembly_timestamp": "2026-03-11T12:00:00+00:00",
            "packet_version": "1.0",
            "stage_key": "orchestration",
            "policy_outcome": "eligible",
            "policy_version": "1.0",
            "downstream_usable": True,
        }),
    }


def _write_decision_packet(
    store, run_id, candidate_id, packet=None, **overrides,
):
    """Write a Step 12 per-candidate decision packet artifact."""
    if packet is None:
        packet = _make_decision_packet(
            candidate_id=candidate_id,
            run_id=run_id,
            **overrides,
        )
    art = build_artifact_record(
        run_id=run_id,
        stage_key="orchestration",
        artifact_key=f"decision_{candidate_id}",
        artifact_type="decision_packet",
        data=packet,
        candidate_id=candidate_id,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _write_decision_packet_summary(
    store, run_id, candidate_ids, **overrides,
):
    """Write a Step 12 decision_packet_summary artifact."""
    records = [
        {"candidate_id": cid, "packet_status": "assembled"}
        for cid in candidate_ids
    ]
    data = {
        "stage_key": "orchestration",
        "stage_status": overrides.get("stage_status", "success"),
        "total_candidates_in": len(candidate_ids),
        "total_assembled": len(candidate_ids),
        "total_degraded": 0,
        "total_failed": 0,
        "candidate_ids_processed": list(candidate_ids),
        "assembly_records": records,
        "output_artifact_refs": {
            cid: f"art-dp-{cid}" for cid in candidate_ids
        },
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="orchestration",
        artifact_key="decision_packet_summary",
        artifact_type="decision_packet_summary",
        data=data,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _populate_decision_packets(
    store, run_id, candidate_ids,
    *,
    packet_overrides=None,
):
    """Write decision_packet_summary + per-candidate decision packets."""
    _write_decision_packet_summary(store, run_id, candidate_ids)
    for cid in candidate_ids:
        overrides = (packet_overrides or {}).get(cid, {})
        _write_decision_packet(store, run_id, cid, **overrides)


# =====================================================================
#  Vocabulary / constant tests
# =====================================================================

class TestConstants:
    def test_stage_key(self):
        assert _STAGE_KEY == "prompt_payload"

    def test_stage_in_pipeline(self):
        assert "prompt_payload" in PIPELINE_STAGES

    def test_prompt_payload_version(self):
        assert _PROMPT_PAYLOAD_VERSION == "1.0"

    def test_payload_statuses(self):
        assert PAYLOAD_BUILT == "built"
        assert PAYLOAD_BUILT_DEGRADED == "built_degraded"
        assert PAYLOAD_FAILED == "failed"

    def test_valid_payload_statuses_complete(self):
        assert {PAYLOAD_BUILT, PAYLOAD_BUILT_DEGRADED, PAYLOAD_FAILED} == \
            VALID_PAYLOAD_STATUSES

    def test_event_types_registered(self):
        for et in (
            "prompt_payload_started",
            "prompt_payload_completed",
            "prompt_payload_failed",
        ):
            assert et in VALID_EVENT_TYPES, f"{et} not in VALID_EVENT_TYPES"

    def test_artifact_types_registered(self):
        assert "prompt_payload" in VALID_ARTIFACT_TYPES
        assert "prompt_payload_summary" in VALID_ARTIFACT_TYPES


# =====================================================================
#  Candidate section compression tests
# =====================================================================

class TestCompressCandidateSection:
    def test_basic_compression(self):
        section = _make_candidate_section()
        compact, trimmed = compress_candidate_section(section)
        assert compact["candidate_id"] == "c1"
        assert compact["symbol"] == "SPY"
        assert compact["strategy_type"] == "put_credit_spread"
        assert compact["direction"] == "long"
        assert compact["rank_position"] == 1
        assert compact["rank_score"] == 0.85
        assert compact["confidence"] == 0.8
        assert compact["enrichment_status"] == "full"

    def test_none_input(self):
        compact, trimmed = compress_candidate_section(None)
        assert compact == {}
        assert "candidate_section_missing" in trimmed

    def test_context_summary_present(self):
        section = _make_candidate_section(
            compact_context_summary={"regime": "bearish"},
        )
        compact, trimmed = compress_candidate_section(section)
        assert compact["context_summary"] == {"regime": "bearish"}
        assert "compact_context_summary" not in trimmed

    def test_context_summary_absent(self):
        section = _make_candidate_section(compact_context_summary=None)
        compact, trimmed = compress_candidate_section(section)
        assert "context_summary" not in compact
        assert "compact_context_summary" in trimmed

    def test_enrichment_notes_short(self):
        section = _make_candidate_section(enrichment_notes=["note1", "note2"])
        compact, _ = compress_candidate_section(section)
        assert compact["enrichment_notes"] == ["note1", "note2"]

    def test_enrichment_notes_truncated(self):
        notes = [f"note{i}" for i in range(10)]
        section = _make_candidate_section(enrichment_notes=notes)
        compact, trimmed = compress_candidate_section(section)
        assert len(compact["enrichment_notes"]) == 3
        assert any("truncated" in t for t in trimmed)

    def test_scanner_key_kept(self):
        section = _make_candidate_section(scanner_key="my_scanner")
        compact, _ = compress_candidate_section(section)
        assert compact["scanner_key"] == "my_scanner"


# =====================================================================
#  Event section compression tests
# =====================================================================

class TestCompressEventSection:
    def test_available_event(self):
        section = _make_event_section()
        compact, trimmed = compress_event_section(section)
        assert compact["event_data_available"] is True
        assert compact["nearest_event_type"] == "earnings"
        assert compact["nearest_days_until"] == 9
        assert len(trimmed) == 0

    def test_unavailable_event(self):
        section = _make_event_section(available=False)
        compact, trimmed = compress_event_section(section)
        assert compact["event_data_available"] is False
        assert compact["degraded_reasons"] == [
            "event context not available",
        ]

    def test_none_input(self):
        compact, trimmed = compress_event_section(None)
        assert compact["event_data_available"] is False
        assert "event_section_missing" in trimmed

    def test_risk_flags_preserved(self):
        section = _make_event_section(risk_flags=["earnings_within_5d"])
        compact, _ = compress_event_section(section)
        assert compact["risk_flags"] == ["earnings_within_5d"]

    def test_large_summary_trimmed(self):
        big_summary = {f"key_{i}": f"val_{i}" for i in range(15)}
        section = _make_event_section(event_summary=big_summary)
        compact, trimmed = compress_event_section(section)
        assert len(compact["event_summary"]) < 15
        assert any("trimmed" in t for t in trimmed)

    def test_degraded_reasons_preserved(self):
        section = _make_event_section(
            degraded_reasons=["partial provider failure"],
        )
        compact, _ = compress_event_section(section)
        assert compact["degraded_reasons"] == ["partial provider failure"]


# =====================================================================
#  Policy section compression tests
# =====================================================================

class TestCompressPolicySection:
    def test_basic_eligible(self):
        section = _make_policy_section()
        compact, trimmed = compress_policy_section(section)
        assert compact["overall_outcome"] == "eligible"
        assert compact["downstream_usable"] is True
        assert compact["blocking_reasons"] == []
        assert compact["caution_reasons"] == []

    def test_none_input(self):
        compact, trimmed = compress_policy_section(None)
        assert compact == {}
        assert "policy_section_missing" in trimmed

    def test_blockers_preserved(self):
        section = _make_policy_section(
            overall_outcome="blocked",
            blocking_reasons=["max_positions_reached"],
        )
        compact, _ = compress_policy_section(section)
        assert compact["overall_outcome"] == "blocked"
        assert compact["blocking_reasons"] == ["max_positions_reached"]

    def test_cautions_preserved(self):
        section = _make_policy_section(
            caution_reasons=["high_volatility"],
        )
        compact, _ = compress_policy_section(section)
        assert compact["caution_reasons"] == ["high_volatility"]

    def test_restrictions_preserved(self):
        section = _make_policy_section(
            restriction_reasons=["sector_limit_reached"],
        )
        compact, _ = compress_policy_section(section)
        assert compact["restriction_reasons"] == ["sector_limit_reached"]

    def test_checks_summarized(self):
        checks = [
            {"check_name": "capital", "passed": True},
            {"check_name": "position", "passed": True},
            {"check_name": "event_risk", "passed": False, "reason": "close"},
        ]
        section = _make_policy_section(checks=checks)
        compact, trimmed = compress_policy_section(section)
        assert compact["check_summary"]["total"] == 3
        assert compact["check_summary"]["passed"] == 2
        assert compact["check_summary"]["failed"] == 1
        assert len(compact["failing_checks"]) == 1
        assert compact["failing_checks"][0]["check_name"] == "event_risk"
        # Passing checks were summarized
        assert any("passing_checks" in t for t in trimmed)

    def test_eligibility_flags_kept(self):
        section = _make_policy_section(
            eligibility_flags={"trade_capable": True},
        )
        compact, _ = compress_policy_section(section)
        assert compact["eligibility_flags"]["trade_capable"] is True

    def test_portfolio_context_kept(self):
        section = _make_policy_section(
            portfolio_context_summary={"capital_util": 30.0},
        )
        compact, _ = compress_policy_section(section)
        assert compact["portfolio_context_summary"] == {"capital_util": 30.0}

    def test_event_risk_summary_kept(self):
        section = _make_policy_section(
            event_risk_summary={"risk_flags": ["earnings"]},
        )
        compact, _ = compress_policy_section(section)
        assert compact["event_risk_summary"] == {"risk_flags": ["earnings"]}


# =====================================================================
#  Quality section compression tests
# =====================================================================

class TestCompressQualitySection:
    def test_basic_healthy(self):
        section = _make_quality_section()
        compact, trimmed = compress_quality_section(section)
        assert compact["downstream_usable"] is True
        assert compact["missing_sections"] == []
        assert compact["degraded_sections"] == []
        assert len(trimmed) == 0

    def test_none_input(self):
        compact, trimmed = compress_quality_section(None)
        assert compact["downstream_usable"] is False
        assert "quality_section_missing" in trimmed

    def test_degraded_sections(self):
        section = _make_quality_section(
            event_status="degraded",
            downstream_usable=True,
        )
        compact, _ = compress_quality_section(section)
        assert "event_section" in compact["degraded_sections"]

    def test_missing_sections(self):
        section = _make_quality_section(event_status="missing")
        compact, _ = compress_quality_section(section)
        assert "event_section" in compact["missing_sections"]

    def test_degraded_reasons_preserved(self):
        section = _make_quality_section(
            degraded_reasons=["partial failure"],
        )
        compact, _ = compress_quality_section(section)
        assert "partial failure" in compact["degraded_reasons"]


# =====================================================================
#  Prompt payload assembly tests
# =====================================================================

class TestBuildPromptPayload:
    def test_basic_shape(self):
        packet = _make_decision_packet()
        payload = build_prompt_payload(
            decision_packet=packet,
            run_id="test-pp-001",
            decision_packet_artifact_ref="art-dp-001",
        )
        assert payload["prompt_payload_version"] == "1.0"
        assert payload["run_id"] == "test-pp-001"
        assert payload["candidate_id"] == "c1"
        assert payload["symbol"] == "SPY"
        assert payload["payload_status"] == PAYLOAD_BUILT
        assert payload["source_decision_packet_ref"] == "art-dp-001"
        assert isinstance(payload["compact_candidate_block"], dict)
        assert isinstance(payload["compact_event_block"], dict)
        assert isinstance(payload["compact_policy_block"], dict)
        assert isinstance(payload["compact_quality_block"], dict)
        assert isinstance(payload["rendered_prompt_text"], str)
        assert isinstance(payload["compression_metadata"], dict)
        assert isinstance(payload["source_section_refs"], dict)
        assert isinstance(payload["downstream_usable"], bool)
        assert isinstance(payload["warnings"], list)
        assert isinstance(payload["degraded_reasons"], list)
        assert isinstance(payload["metadata"], dict)

    def test_degraded_packet_produces_degraded_payload(self):
        packet = _make_decision_packet(
            packet_status="assembled_degraded",
            quality_section=_make_quality_section(
                event_status="missing",
            ),
        )
        payload = build_prompt_payload(
            decision_packet=packet,
            run_id="test-pp-001",
            decision_packet_artifact_ref="art-dp-001",
        )
        assert payload["payload_status"] == PAYLOAD_BUILT_DEGRADED
        assert any("degraded" in w for w in payload["warnings"])

    def test_source_refs_lineage(self):
        payload = build_prompt_payload(
            decision_packet=_make_decision_packet(),
            run_id="test-pp-001",
            decision_packet_artifact_ref="art-dp-123",
        )
        refs = payload["source_section_refs"]
        assert refs["decision_packet_ref"] == "art-dp-123"
        assert refs["decision_packet_version"] == "1.0"

    def test_compression_metadata(self):
        payload = build_prompt_payload(
            decision_packet=_make_decision_packet(),
            run_id="test-pp-001",
            decision_packet_artifact_ref=None,
        )
        cm = payload["compression_metadata"]
        assert "sections_compressed" in cm
        assert "trimmed_fields" in cm
        assert "payload_version" in cm

    def test_metadata_fields(self):
        payload = build_prompt_payload(
            decision_packet=_make_decision_packet(),
            run_id="test-pp-001",
            decision_packet_artifact_ref=None,
        )
        m = payload["metadata"]
        assert m["stage_key"] == "prompt_payload"
        assert m["payload_version"] == "1.0"
        assert "policy_outcome" in m
        assert "assembly_timestamp" in m

    def test_policy_outcome_preserved_in_metadata(self):
        packet = _make_decision_packet(
            policy_section=_make_policy_section(
                overall_outcome="blocked",
            ),
        )
        payload = build_prompt_payload(
            decision_packet=packet,
            run_id="test-pp-001",
            decision_packet_artifact_ref=None,
        )
        assert payload["metadata"]["policy_outcome"] == "blocked"


# =====================================================================
#  Rendered prompt text tests
# =====================================================================

class TestRenderedPromptText:
    def test_basic_render(self):
        text = render_prompt_text(
            compact_candidate={"symbol": "SPY", "strategy_type": "pcs",
                               "direction": "long"},
            compact_event={"event_data_available": True,
                           "nearest_event_type": "earnings",
                           "nearest_days_until": 5, "risk_flags": []},
            compact_policy={"overall_outcome": "eligible",
                            "blocking_reasons": [], "caution_reasons": [],
                            "restriction_reasons": [],
                            "check_summary": {"total": 2, "passed": 2,
                                              "failed": 0}},
            compact_quality={"downstream_usable": True,
                             "missing_sections": [],
                             "degraded_sections": []},
        )
        assert "SPY" in text
        assert "eligible" in text.upper() or "eligible" in text
        assert "DOWNSTREAM USABLE: True" in text

    def test_blocked_render(self):
        text = render_prompt_text(
            compact_candidate={"symbol": "QQQ", "strategy_type": "pcs",
                               "direction": "long"},
            compact_event={"event_data_available": False},
            compact_policy={"overall_outcome": "blocked",
                            "blocking_reasons": ["max_positions"],
                            "caution_reasons": [],
                            "restriction_reasons": [],
                            "check_summary": {}},
            compact_quality={"downstream_usable": False,
                             "missing_sections": ["event_section"],
                             "degraded_sections": []},
        )
        assert "blocked" in text.upper() or "blocked" in text
        assert "max_positions" in text
        assert "MISSING SECTIONS" in text

    def test_unavailable_events(self):
        text = render_prompt_text(
            compact_candidate={"symbol": "IWM", "strategy_type": "pcs",
                               "direction": "short"},
            compact_event={"event_data_available": False},
            compact_policy={"overall_outcome": "eligible",
                            "blocking_reasons": [],
                            "caution_reasons": [],
                            "restriction_reasons": [],
                            "check_summary": {}},
            compact_quality={"downstream_usable": True,
                             "missing_sections": [],
                             "degraded_sections": []},
        )
        assert "unavailable" in text.lower()


# =====================================================================
#  Handler contract tests
# =====================================================================

class TestHandlerContract:
    def test_return_shape_keys(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert set(result.keys()) == {
            "outcome", "summary_counts", "artifacts",
            "metadata", "error",
        }

    def test_successful_outcome(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"
        assert result["error"] is None

    def test_summary_counts_shape(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        sc = result["summary_counts"]
        assert "total_built" in sc
        assert "total_degraded" in sc
        assert "total_failed" in sc

    def test_metadata_has_stage_summary(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert "stage_summary" in result["metadata"]


# =====================================================================
#  Single candidate processing
# =====================================================================

class TestSingleCandidate:
    def test_single_candidate_success(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_built"] == 1
        assert len(result["artifacts"]) == 1

    def test_payload_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        assert art is not None
        data = art["data"]
        assert data["candidate_id"] == "c1"
        assert data["payload_status"] == PAYLOAD_BUILT

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(
            store, "prompt_payload", "prompt_payload_summary",
        )
        assert art is not None
        data = art["data"]
        assert data["total_built"] == 1
        assert data["total_failed"] == 0


# =====================================================================
#  Multiple candidate processing
# =====================================================================

class TestMultipleCandidates:
    def test_two_candidates(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1", "c2"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_built"] == 2
        assert len(result["artifacts"]) == 2

    def test_each_candidate_gets_artifact(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1", "c2", "c3"])
        prompt_payload_handler(run, store, "prompt_payload")
        for cid in ("c1", "c2", "c3"):
            art = get_artifact_by_key(
                store, "prompt_payload", f"prompt_{cid}",
            )
            assert art is not None, f"No payload artifact for {cid}"


# =====================================================================
#  Blocked candidate still produces valid payload
# =====================================================================

class TestBlockedCandidatePayload:
    def test_blocked_candidate_builds_payload(self):
        run, store = _make_run_and_store()
        blocked_packet = _make_decision_packet(
            candidate_id="c1",
            policy_section=_make_policy_section(
                overall_outcome="blocked",
                blocking_reasons=["max_positions_reached"],
                downstream_usable=False,
            ),
        )
        _write_decision_packet_summary(store, run["run_id"], ["c1"])
        _write_decision_packet(store, run["run_id"], "c1", blocked_packet)

        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_built"] == 1

        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        data = art["data"]
        assert data["payload_status"] in (PAYLOAD_BUILT, PAYLOAD_BUILT_DEGRADED)
        # Policy guardrails must remain
        assert data["compact_policy_block"]["overall_outcome"] == "blocked"
        assert "max_positions_reached" in \
            data["compact_policy_block"]["blocking_reasons"]


# =====================================================================
#  Restricted candidate still produces valid payload
# =====================================================================

class TestRestrictedCandidatePayload:
    def test_restricted_candidate_builds_payload(self):
        run, store = _make_run_and_store()
        restricted_packet = _make_decision_packet(
            candidate_id="c1",
            policy_section=_make_policy_section(
                overall_outcome="restricted",
                restriction_reasons=["sector_limit_near"],
            ),
        )
        _write_decision_packet_summary(store, run["run_id"], ["c1"])
        _write_decision_packet(store, run["run_id"], "c1", restricted_packet)

        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        data = art["data"]
        assert data["compact_policy_block"]["overall_outcome"] == "restricted"
        assert "sector_limit_near" in \
            data["compact_policy_block"]["restriction_reasons"]


# =====================================================================
#  Degraded event section compresses honestly
# =====================================================================

class TestDegradedEventCompression:
    def test_degraded_event_preserved(self):
        run, store = _make_run_and_store()
        packet = _make_decision_packet(
            candidate_id="c1",
            packet_status="assembled_degraded",
            event_section=_make_event_section(available=False),
            quality_section=_make_quality_section(
                event_status="missing",
                downstream_usable=True,
            ),
        )
        _write_decision_packet_summary(store, run["run_id"], ["c1"])
        _write_decision_packet(store, run["run_id"], "c1", packet)

        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        data = art["data"]
        assert data["payload_status"] == PAYLOAD_BUILT_DEGRADED
        assert data["compact_event_block"]["event_data_available"] is False
        # Quality section should show missing event
        assert "event_section" in \
            data["compact_quality_block"]["missing_sections"]


# =====================================================================
#  Quality section propagates into payload
# =====================================================================

class TestQualityPropagation:
    def test_quality_downstream_usable(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        data = art["data"]
        assert data["downstream_usable"] is True
        assert data["compact_quality_block"]["downstream_usable"] is True

    def test_quality_not_usable_propagates(self):
        run, store = _make_run_and_store()
        packet = _make_decision_packet(
            candidate_id="c1",
            quality_section=_make_quality_section(
                downstream_usable=False,
                event_status="missing",
            ),
        )
        _write_decision_packet_summary(store, run["run_id"], ["c1"])
        _write_decision_packet(store, run["run_id"], "c1", packet)

        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        assert art["data"]["downstream_usable"] is False


# =====================================================================
#  Policy guardrails remain explicit
# =====================================================================

class TestPolicyGuardrailsExplicit:
    def test_blockers_not_diluted(self):
        section = _make_policy_section(
            overall_outcome="blocked",
            blocking_reasons=["exceeds_max_loss", "sector_overlimit"],
        )
        compact, _ = compress_policy_section(section)
        assert compact["blocking_reasons"] == [
            "exceeds_max_loss", "sector_overlimit",
        ]
        assert compact["overall_outcome"] == "blocked"

    def test_cautions_not_diluted(self):
        section = _make_policy_section(
            caution_reasons=["high_iv", "pre_earnings"],
        )
        compact, _ = compress_policy_section(section)
        assert compact["caution_reasons"] == ["high_iv", "pre_earnings"]

    def test_failing_checks_preserved_in_full(self):
        failing_check = {
            "check_name": "event_risk",
            "passed": False,
            "reason": "earnings within 3 days",
            "threshold": 5,
            "actual": 3,
        }
        section = _make_policy_section(checks=[
            {"check_name": "capital", "passed": True},
            failing_check,
        ])
        compact, _ = compress_policy_section(section)
        assert len(compact["failing_checks"]) == 1
        fc = compact["failing_checks"][0]
        assert fc["check_name"] == "event_risk"
        assert fc["reason"] == "earnings within 3 days"


# =====================================================================
#  Missing decision packet summary
# =====================================================================

class TestMissingDecisionPacketSummary:
    def test_no_summary_fails(self):
        run, store = _make_run_and_store()
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_DECISION_PACKET_SOURCE"

    def test_no_summary_emits_failed(self):
        run, store = _make_run_and_store()
        events = []
        result = prompt_payload_handler(
            run, store, "prompt_payload",
            event_callback=events.append,
        )
        assert result["outcome"] == "failed"
        types = [e["event_type"] for e in events]
        assert "prompt_payload_failed" in types


# =====================================================================
#  Empty candidate set
# =====================================================================

class TestVacuousCompletion:
    def test_zero_candidates(self):
        run, store = _make_run_and_store()
        _write_decision_packet_summary(store, run["run_id"], [])
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"
        assert result["metadata"]["stage_status"] == "no_candidates_to_process"

    def test_vacuous_summary_artifact(self):
        run, store = _make_run_and_store()
        _write_decision_packet_summary(store, run["run_id"], [])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(
            store, "prompt_payload", "prompt_payload_summary",
        )
        assert art is not None
        assert art["data"]["total_built"] == 0


# =====================================================================
#  Missing per-candidate decision packet
# =====================================================================

class TestMissingPerCandidatePacket:
    def test_single_missing_packet_fails(self):
        run, store = _make_run_and_store()
        # Summary says c1 exists, but no packet written
        _write_decision_packet_summary(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["total_failed"] == 1

    def test_one_missing_one_present(self):
        run, store = _make_run_and_store()
        _write_decision_packet_summary(store, run["run_id"], ["c1", "c2"])
        # Only write c1
        _write_decision_packet(store, run["run_id"], "c1")
        result = prompt_payload_handler(run, store, "prompt_payload")
        # c2 missing → 1 failed, 1 built → degraded outcome still completed
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_built"] == 1
        assert result["summary_counts"]["total_failed"] == 1


# =====================================================================
#  Partial failures
# =====================================================================

class TestPartialFailures:
    def test_some_fail_some_succeed(self):
        run, store = _make_run_and_store()
        _write_decision_packet_summary(
            store, run["run_id"], ["c1", "c2", "c3"],
        )
        _write_decision_packet(store, run["run_id"], "c1")
        _write_decision_packet(store, run["run_id"], "c2")
        # c3 missing
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_built"] == 2
        assert result["summary_counts"]["total_failed"] == 1

    def test_all_fail(self):
        run, store = _make_run_and_store()
        _write_decision_packet_summary(store, run["run_id"], ["c1", "c2"])
        # No packets written → all fail
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "PROMPT_PAYLOAD_ALL_FAILED"


# =====================================================================
#  Artifact creation and lineage
# =====================================================================

class TestArtifactWriting:
    def test_per_candidate_artifact_key_pattern(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        assert art is not None
        assert art["artifact_type"] == "prompt_payload"
        assert art["candidate_id"] == "c1"

    def test_summary_artifact_key(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(
            store, "prompt_payload", "prompt_payload_summary",
        )
        assert art is not None
        assert art["artifact_type"] == "prompt_payload_summary"

    def test_source_ref_links_to_decision_packet(self):
        run, store = _make_run_and_store()
        dp_art_id = _write_decision_packet(store, run["run_id"], "c1")
        _write_decision_packet_summary(store, run["run_id"], ["c1"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        payload = art["data"]
        assert payload["source_decision_packet_ref"] == dp_art_id

    def test_handler_returns_artifact_ids(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1", "c2"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert len(result["artifacts"]) == 2


# =====================================================================
#  Stage summary contents
# =====================================================================

class TestStageSummary:
    def test_summary_fields(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1", "c2"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        summary = result["metadata"]["stage_summary"]
        assert summary["stage_key"] == "prompt_payload"
        assert summary["total_candidates_in"] == 2
        assert summary["total_built"] == 2
        assert summary["total_failed"] == 0
        assert len(summary["payload_records"]) == 2
        assert "compression_stats" in summary
        assert "policy_outcome_counts" in summary

    def test_summary_candidate_ids(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1", "c2"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        summary = result["metadata"]["stage_summary"]
        assert set(summary["candidate_ids_processed"]) == {"c1", "c2"}

    def test_summary_output_artifact_refs(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        summary = result["metadata"]["stage_summary"]
        assert "c1" in summary["output_artifact_refs"]

    def test_compression_stats(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        cs = result["metadata"]["stage_summary"]["compression_stats"]
        assert "total_trimmed_fields" in cs
        assert "candidates_with_trimming" in cs


# =====================================================================
#  Compression metadata tests
# =====================================================================

class TestCompressionMetadata:
    def test_compression_metadata_in_payload(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        cm = art["data"]["compression_metadata"]
        assert "sections_compressed" in cm
        assert "trimmed_fields" in cm

    def test_trimmed_fields_tracked(self):
        # Candidate with many enrichment notes → triggers truncation
        packet = _make_decision_packet(
            candidate_id="c1",
            candidate_section=_make_candidate_section(
                enrichment_notes=[f"note{i}" for i in range(10)],
            ),
        )
        payload = build_prompt_payload(
            decision_packet=packet,
            run_id="test-pp-001",
            decision_packet_artifact_ref=None,
        )
        assert any(
            "truncated" in t
            for t in payload["compression_metadata"]["trimmed_fields"]
        )


# =====================================================================
#  Event emission tests
# =====================================================================

class TestEventEmission:
    def test_started_event(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        events = []
        prompt_payload_handler(
            run, store, "prompt_payload",
            event_callback=events.append,
        )
        types = [e["event_type"] for e in events]
        assert "prompt_payload_started" in types

    def test_completed_event(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        events = []
        prompt_payload_handler(
            run, store, "prompt_payload",
            event_callback=events.append,
        )
        types = [e["event_type"] for e in events]
        assert "prompt_payload_completed" in types

    def test_failed_event_on_missing_summary(self):
        run, store = _make_run_and_store()
        events = []
        prompt_payload_handler(
            run, store, "prompt_payload",
            event_callback=events.append,
        )
        types = [e["event_type"] for e in events]
        assert "prompt_payload_failed" in types

    def test_completed_event_metadata(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1", "c2"])
        events = []
        prompt_payload_handler(
            run, store, "prompt_payload",
            event_callback=events.append,
        )
        completed = [
            e for e in events
            if e["event_type"] == "prompt_payload_completed"
        ]
        assert len(completed) == 1
        meta = completed[0]["metadata"]
        assert meta["total_built"] == 2

    def test_no_events_without_callback(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        # No event_callback → should not crash
        result = prompt_payload_handler(run, store, "prompt_payload")
        assert result["outcome"] == "completed"


# =====================================================================
#  Orchestrator integration
# =====================================================================

class TestOrchestratorIntegration:
    def test_default_handler_wired(self):
        handlers = get_default_handlers()
        assert handlers["prompt_payload"] is prompt_payload_handler

    def test_dependency_map(self):
        deps = get_default_dependency_map()
        assert deps["prompt_payload"] == ["orchestration"]

    def test_pipeline_runs_through_prompt_payload(self):
        """Full stub pipeline runs through prompt_payload without error."""
        result = _all_stub_pipeline(run_id="run-int-001")
        # All stages should complete (all stubs)
        assert result["run"]["status"] == "completed"

    def test_execute_stage_integration(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = execute_stage(
            run, store, "prompt_payload",
            handler=prompt_payload_handler,
        )
        assert result["outcome"] == "completed"


# =====================================================================
#  Forward compatibility for final model execution stage
# =====================================================================

class TestForwardCompatibility:
    def test_payload_has_model_input_sections(self):
        """Verify payload structure supports downstream model consumption."""
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(store, "prompt_payload", "prompt_c1")
        data = art["data"]
        # All compact blocks present
        assert "compact_candidate_block" in data
        assert "compact_event_block" in data
        assert "compact_policy_block" in data
        assert "compact_quality_block" in data
        # Rendered text available
        assert "rendered_prompt_text" in data
        assert isinstance(data["rendered_prompt_text"], str)
        # Source refs for audit
        assert "source_section_refs" in data
        assert "source_decision_packet_ref" in data

    def test_summary_supports_iteration(self):
        """Stage summary has candidate_ids and artifact refs for iteration."""
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1", "c2"])
        prompt_payload_handler(run, store, "prompt_payload")
        art = get_artifact_by_key(
            store, "prompt_payload", "prompt_payload_summary",
        )
        data = art["data"]
        assert set(data["candidate_ids_processed"]) == {"c1", "c2"}
        assert "c1" in data["output_artifact_refs"]
        assert "c2" in data["output_artifact_refs"]

    def test_payload_retrievable_by_candidate(self):
        """Each payload is retrievable by candidate_id."""
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1", "c2"])
        prompt_payload_handler(run, store, "prompt_payload")
        for cid in ("c1", "c2"):
            art = get_artifact_by_key(
                store, "prompt_payload", f"prompt_{cid}",
            )
            assert art is not None
            assert art["data"]["candidate_id"] == cid


# =====================================================================
#  Payload record tests
# =====================================================================

class TestPayloadRecords:
    def test_record_shape(self):
        run, store = _make_run_and_store()
        _populate_decision_packets(store, run["run_id"], ["c1"])
        result = prompt_payload_handler(run, store, "prompt_payload")
        records = result["metadata"]["stage_summary"]["payload_records"]
        assert len(records) == 1
        rec = records[0]
        assert rec["candidate_id"] == "c1"
        assert rec["payload_status"] == PAYLOAD_BUILT
        assert isinstance(rec["included_sections"], list)
        assert isinstance(rec["trimmed_fields"], list)
        assert isinstance(rec["degraded_reasons"], list)
        assert rec["output_artifact_ref"] is not None
        assert rec["downstream_usable"] is True
        assert rec["error"] is None

    def test_failed_record(self):
        run, store = _make_run_and_store()
        _write_decision_packet_summary(store, run["run_id"], ["c1"])
        # No packet for c1
        result = prompt_payload_handler(run, store, "prompt_payload")
        records = result["metadata"]["stage_summary"]["payload_records"]
        rec = records[0]
        assert rec["payload_status"] == PAYLOAD_FAILED
        assert rec["error"] is not None
        assert rec["error"]["code"] == "DECISION_PACKET_MISSING"

    def test_policy_outcome_in_record(self):
        run, store = _make_run_and_store()
        packet = _make_decision_packet(
            candidate_id="c1",
            policy_section=_make_policy_section(
                overall_outcome="blocked",
            ),
        )
        _write_decision_packet_summary(store, run["run_id"], ["c1"])
        _write_decision_packet(store, run["run_id"], "c1", packet)
        result = prompt_payload_handler(run, store, "prompt_payload")
        records = result["metadata"]["stage_summary"]["payload_records"]
        assert records[0]["policy_outcome"] == "blocked"


# =====================================================================
#  Section availability in summary
# =====================================================================

class TestSectionAvailability:
    def test_policy_outcome_counts(self):
        run, store = _make_run_and_store()
        p1 = _make_decision_packet(
            candidate_id="c1",
            policy_section=_make_policy_section(
                overall_outcome="eligible",
            ),
        )
        p2 = _make_decision_packet(
            candidate_id="c2", symbol="QQQ",
            policy_section=_make_policy_section(
                overall_outcome="blocked",
            ),
        )
        _write_decision_packet_summary(store, run["run_id"], ["c1", "c2"])
        _write_decision_packet(store, run["run_id"], "c1", p1)
        _write_decision_packet(store, run["run_id"], "c2", p2)

        result = prompt_payload_handler(run, store, "prompt_payload")
        counts = result["metadata"]["stage_summary"]["policy_outcome_counts"]
        assert counts.get("eligible") == 1
        assert counts.get("blocked") == 1
