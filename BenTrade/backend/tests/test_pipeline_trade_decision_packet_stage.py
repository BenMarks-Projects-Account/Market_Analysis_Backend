"""Tests for pipeline_trade_decision_packet_stage — Step 12.

Covers:
  - Vocabulary/constant registration
  - Candidate section builder
  - Event section builder
  - Policy section builder
  - Quality section builder
  - Decision packet assembly
  - Handler contract (return shape, summary_counts, metadata)
  - Single-candidate processing
  - Multi-candidate processing
  - Blocked candidate produces valid packet
  - Restricted candidate produces valid packet
  - Vacuous completion (no enrichment summary, no candidates)
  - Missing candidate enrichment summary
  - Missing policy summary
  - Missing enriched packet per candidate
  - Missing policy output per candidate
  - Missing event context (degraded, not failure)
  - Partial candidate failures
  - Artifact creation and lineage
  - Stage summary structure
  - Event emission (started/completed/failed)
  - Orchestrator integration (wiring, deps, pipeline run)
  - Forward compatibility for prompt payload building
"""

import pytest

from app.services.pipeline_trade_decision_packet_stage import (
    _STAGE_KEY,
    _DECISION_PACKET_VERSION,
    PACKET_ASSEMBLED,
    PACKET_ASSEMBLED_DEGRADED,
    PACKET_FAILED,
    VALID_PACKET_STATUSES,
    SECTION_PRESENT,
    SECTION_MISSING,
    SECTION_DEGRADED,
    build_candidate_section,
    build_event_section,
    build_policy_section,
    build_quality_section,
    assemble_decision_packet,
    decision_packet_handler,
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

def _make_run_and_store(run_id="test-dp-001"):
    """Create a fresh run+store with upstream stages completed.

    Completes through policy (since policy runs before orchestration
    in the canonical stage order).
    """
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    for stage in (
        "market_data", "market_model_analysis",
        "scanners", "candidate_selection",
        "shared_context", "candidate_enrichment",
        "events", "policy",
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
    store, run_id, candidate_id, symbol="SPY", **extra,
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
        "compact_context_summary": extra.get("compact_context_summary", {}),
        "enrichment_status": extra.get("enrichment_status", "full"),
        "enrichment_notes": extra.get("enrichment_notes", []),
        "event_context": None,
        "portfolio_context": None,
        "policy_context": None,
        "decision_packet": None,
        "prompt_payload": None,
        "final_response": None,
        "enriched_at": "2026-03-11T12:00:00+00:00",
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="candidate_enrichment",
        artifact_key=f"enriched_{candidate_id}",
        artifact_type="enriched_candidate",
        data=data,
        candidate_id=candidate_id,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _write_enrichment_summary(store, run_id, candidate_ids):
    """Write a Step 9 candidate_enrichment_summary artifact."""
    records = [
        {"candidate_id": cid, "enrichment_status": "full"}
        for cid in candidate_ids
    ]
    data = {
        "stage_key": "candidate_enrichment",
        "enrichment_records": records,
        "total_enriched": len(candidate_ids),
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="candidate_enrichment",
        artifact_key="candidate_enrichment_summary",
        artifact_type="candidate_enrichment_summary",
        data=data,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _make_policy_output(
    candidate_id="c1",
    symbol="SPY",
    overall_outcome="eligible",
    policy_status="evaluated",
    downstream_usable=True,
    blocking_reasons=None,
    caution_reasons=None,
    restriction_reasons=None,
    checks=None,
    degraded_reasons=None,
):
    """Build a minimal policy output dict for testing."""
    return {
        "policy_version": "1.0",
        "run_id": "test-dp-001",
        "candidate_id": candidate_id,
        "symbol": symbol,
        "source_enriched_candidate_ref": None,
        "source_event_context_ref": None,
        "policy_status": policy_status,
        "overall_outcome": overall_outcome,
        "checks": checks or [],
        "blocking_reasons": blocking_reasons or [],
        "caution_reasons": caution_reasons or [],
        "restriction_reasons": restriction_reasons or [],
        "eligibility_flags": {
            "trade_capable": True,
            "strategy_allowed": True,
            "within_capital_limits": True,
            "within_position_limits": True,
            "no_symbol_overlap": True,
            "event_risk_acceptable": True,
        },
        "portfolio_context_summary": {
            "snapshot_status": "available",
            "trade_capability_status": "enabled",
            "active_symbol_positions": 0,
            "total_active_positions": 0,
            "capital_utilization_pct": 20.0,
            "restriction_count": 0,
        },
        "event_risk_summary": {
            "event_data_available": True,
            "event_status": "enriched",
            "total_events": 2,
            "risk_flag_count": 0,
            "nearest_event_type": None,
            "nearest_days_until": None,
            "risk_flags": [],
        },
        "downstream_usable": downstream_usable,
        "degraded_reasons": degraded_reasons or [],
        "policy_metadata": {
            "thresholds_used": {},
            "check_count": len(checks or []),
            "blocking_count": len(blocking_reasons or []),
            "caution_count": len(caution_reasons or []),
            "restriction_count": len(restriction_reasons or []),
        },
        "generated_at": "2026-03-11T12:01:00+00:00",
    }


def _write_policy_artifact(store, run_id, candidate_id, policy_output=None):
    """Write a Step 11 policy artifact."""
    if policy_output is None:
        policy_output = _make_policy_output(candidate_id=candidate_id)
    art = build_artifact_record(
        run_id=run_id,
        stage_key="policy",
        artifact_key=f"policy_{candidate_id}",
        artifact_type="policy_output",
        data=policy_output,
        candidate_id=candidate_id,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _write_policy_summary(store, run_id, candidate_ids):
    """Write a Step 11 policy_stage_summary artifact."""
    data = {
        "stage_key": "policy",
        "stage_status": "success",
        "total_candidates_in": len(candidate_ids),
        "total_evaluated": len(candidate_ids),
        "candidate_ids_processed": list(candidate_ids),
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="policy",
        artifact_key="policy_stage_summary",
        artifact_type="policy_stage_summary",
        data=data,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _make_event_context(
    candidate_id="c1",
    symbol="SPY",
    event_status="enriched",
    degraded_reasons=None,
):
    """Build a minimal event context dict for testing."""
    return {
        "event_context_version": "1.0",
        "run_id": "test-dp-001",
        "candidate_id": candidate_id,
        "symbol": symbol,
        "event_status": event_status,
        "event_summary": {
            "total_events": 2,
            "upcoming_count": 1,
            "recent_count": 1,
            "nearest_event_type": "earnings",
            "nearest_event_date": "2026-03-20",
            "nearest_days_until": 9,
            "risk_flag_count": 0,
            "provider_status": "available",
        },
        "upcoming_events": [],
        "recent_events": [],
        "nearest_relevant_event": None,
        "macro_event_context": {},
        "company_event_context": {},
        "expiry_event_context": {},
        "event_risk_flags": [],
        "event_source_refs": {},
        "degraded_reasons": degraded_reasons or [],
        "downstream_usable": True,
        "generated_at": "2026-03-11T12:00:30+00:00",
    }


def _write_event_context(store, run_id, candidate_id, event_data=None):
    """Write a Step 10 event context artifact."""
    if event_data is None:
        event_data = _make_event_context(candidate_id=candidate_id)
    art = build_artifact_record(
        run_id=run_id,
        stage_key="events",
        artifact_key=f"event_{candidate_id}",
        artifact_type="event_context",
        data=event_data,
        candidate_id=candidate_id,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _populate_upstream(
    store,
    run_id,
    candidate_ids,
    *,
    write_events=True,
    write_policy=True,
    policy_overrides=None,
    event_overrides=None,
    enrichment_overrides=None,
):
    """Populate all upstream artifacts for the given candidate IDs."""
    _write_enrichment_summary(store, run_id, candidate_ids)

    for cid in candidate_ids:
        extra = (enrichment_overrides or {}).get(cid, {})
        _write_enriched_candidate(store, run_id, cid, **extra)

    if write_policy:
        _write_policy_summary(store, run_id, candidate_ids)
        for cid in candidate_ids:
            po = (policy_overrides or {}).get(cid)
            _write_policy_artifact(store, run_id, cid, po)

    if write_events:
        for cid in candidate_ids:
            ev = (event_overrides or {}).get(cid)
            _write_event_context(store, run_id, cid, ev)


# =====================================================================
#  Vocabulary / constant tests
# =====================================================================

class TestConstants:

    def test_stage_key(self):
        assert _STAGE_KEY == "orchestration"
        assert _STAGE_KEY in PIPELINE_STAGES

    def test_packet_version(self):
        assert isinstance(_DECISION_PACKET_VERSION, str)

    def test_valid_packet_statuses(self):
        assert PACKET_ASSEMBLED in VALID_PACKET_STATUSES
        assert PACKET_ASSEMBLED_DEGRADED in VALID_PACKET_STATUSES
        assert PACKET_FAILED in VALID_PACKET_STATUSES

    def test_decision_packet_type_registered(self):
        assert "decision_packet" in VALID_ARTIFACT_TYPES

    def test_decision_packet_summary_type_registered(self):
        assert "decision_packet_summary" in VALID_ARTIFACT_TYPES

    def test_event_types_registered(self):
        assert "decision_packet_started" in VALID_EVENT_TYPES
        assert "decision_packet_completed" in VALID_EVENT_TYPES
        assert "decision_packet_failed" in VALID_EVENT_TYPES


# =====================================================================
#  Section builder tests
# =====================================================================

class TestBuildCandidateSection:

    def test_extracts_core_fields(self):
        enriched = {
            "candidate_id": "c1",
            "symbol": "SPY",
            "scanner_key": "sc1",
            "scanner_family": "options",
            "strategy_type": "put_credit_spread",
            "direction": "long",
            "rank_position": 1,
            "rank_score": 0.85,
            "setup_quality": 75.0,
            "confidence": 0.8,
            "enrichment_status": "full",
            "enrichment_notes": [],
            "compact_context_summary": {"overall": "ok"},
        }
        section = build_candidate_section(enriched)
        assert section["candidate_id"] == "c1"
        assert section["symbol"] == "SPY"
        assert section["strategy_type"] == "put_credit_spread"
        assert section["rank_score"] == 0.85
        assert section["enrichment_status"] == "full"
        assert section["compact_context_summary"] == {"overall": "ok"}

    def test_missing_fields_become_none(self):
        section = build_candidate_section({})
        assert section["candidate_id"] is None
        assert section["symbol"] is None
        assert section["enrichment_notes"] == []


class TestBuildEventSection:

    def test_present_event(self):
        event_ctx = _make_event_context()
        section = build_event_section(event_ctx)
        assert section["event_data_available"] is True
        assert section["event_status"] == "enriched"
        assert "total_events" in section["event_summary"]
        assert section["nearest_event_type"] == "earnings"
        assert section["nearest_days_until"] == 9
        assert section["degraded_reasons"] == []

    def test_none_event(self):
        section = build_event_section(None)
        assert section["event_data_available"] is False
        assert section["event_status"] is None
        assert section["event_summary"] == {}
        assert section["risk_flags"] == []
        assert "event context not available" in section["degraded_reasons"]

    def test_event_with_risk_flags(self):
        ctx = _make_event_context()
        ctx["event_risk_flags"] = ["earnings_nearby", "macro_event_nearby"]
        section = build_event_section(ctx)
        assert section["risk_flags"] == [
            "earnings_nearby", "macro_event_nearby",
        ]


class TestBuildPolicySection:

    def test_preserves_authoritative_fields(self):
        po = _make_policy_output(
            overall_outcome="blocked",
            blocking_reasons=["symbol overlap"],
        )
        section = build_policy_section(po)
        assert section["overall_outcome"] == "blocked"
        assert section["blocking_reasons"] == ["symbol overlap"]
        assert section["downstream_usable"] is True
        assert section["policy_version"] == "1.0"
        assert "eligibility_flags" in section
        assert "portfolio_context_summary" in section
        assert "event_risk_summary" in section
        assert "policy_metadata" in section

    def test_empty_policy(self):
        section = build_policy_section({})
        assert section["overall_outcome"] is None
        assert section["checks"] == []
        assert section["downstream_usable"] is False


class TestBuildQualitySection:

    def test_all_present(self):
        enriched = {
            "enrichment_status": "full",
            "enrichment_notes": [],
        }
        policy = _make_policy_output(policy_status="evaluated")
        event = _make_event_context()
        q = build_quality_section(
            enriched_data=enriched,
            policy_output=policy,
            event_ctx=event,
        )
        assert q["section_statuses"]["candidate_section"] == SECTION_PRESENT
        assert q["section_statuses"]["event_section"] == SECTION_PRESENT
        assert q["section_statuses"]["policy_section"] == SECTION_PRESENT
        assert q["missing_sections"] == []
        assert q["degraded_sections"] == []
        assert q["downstream_usable"] is True

    def test_missing_event(self):
        enriched = {"enrichment_status": "full", "enrichment_notes": []}
        policy = _make_policy_output()
        q = build_quality_section(
            enriched_data=enriched,
            policy_output=policy,
            event_ctx=None,
        )
        assert q["section_statuses"]["event_section"] == SECTION_MISSING
        assert "event_section" in q["missing_sections"]
        assert "event context not available" in q["degraded_reasons"]
        # Still usable — events are optional
        assert q["downstream_usable"] is True

    def test_degraded_enrichment(self):
        enriched = {
            "enrichment_status": "degraded",
            "enrichment_notes": ["shared_context is degraded"],
        }
        policy = _make_policy_output()
        event = _make_event_context()
        q = build_quality_section(
            enriched_data=enriched,
            policy_output=policy,
            event_ctx=event,
        )
        assert q["section_statuses"]["candidate_section"] == SECTION_DEGRADED
        assert "candidate_section" in q["degraded_sections"]
        assert "shared_context is degraded" in q["degraded_reasons"]

    def test_degraded_policy(self):
        enriched = {"enrichment_status": "full", "enrichment_notes": []}
        policy = _make_policy_output(
            policy_status="evaluated_degraded",
            degraded_reasons=["event context not available"],
        )
        event = _make_event_context()
        q = build_quality_section(
            enriched_data=enriched,
            policy_output=policy,
            event_ctx=event,
        )
        assert q["section_statuses"]["policy_section"] == SECTION_DEGRADED

    def test_policy_not_usable_means_packet_not_usable(self):
        enriched = {"enrichment_status": "full", "enrichment_notes": []}
        policy = _make_policy_output(downstream_usable=False)
        q = build_quality_section(
            enriched_data=enriched,
            policy_output=policy,
            event_ctx=_make_event_context(),
        )
        assert q["downstream_usable"] is False


# =====================================================================
#  Decision packet assembly tests
# =====================================================================

class TestAssembleDecisionPacket:

    def _assemble(self, **overrides):
        enriched = {
            "candidate_id": "c1",
            "symbol": "SPY",
            "strategy_type": "put_credit_spread",
            "scanner_key": "sc1",
            "scanner_family": "options",
            "direction": "long",
            "rank_position": 1,
            "rank_score": 0.85,
            "setup_quality": 75.0,
            "confidence": 0.8,
            "enrichment_status": "full",
            "enrichment_notes": [],
            "compact_context_summary": {},
            "shared_context_artifact_ref": "art-ctx-001",
        }
        policy = _make_policy_output()
        event = _make_event_context()
        defaults = dict(
            enriched_data=enriched,
            policy_output=policy,
            event_ctx=event,
            run_id="run-001",
            enriched_artifact_ref="art-enr-001",
            policy_artifact_ref="art-pol-001",
            event_artifact_ref="art-evt-001",
        )
        defaults.update(overrides)
        return assemble_decision_packet(**defaults)

    def test_basic_shape(self):
        pkt = self._assemble()
        assert pkt["decision_packet_version"] == _DECISION_PACKET_VERSION
        assert pkt["run_id"] == "run-001"
        assert pkt["candidate_id"] == "c1"
        assert pkt["symbol"] == "SPY"
        assert pkt["packet_status"] == PACKET_ASSEMBLED
        assert "candidate_section" in pkt
        assert "event_section" in pkt
        assert "policy_section" in pkt
        assert "quality_section" in pkt
        assert "source_refs" in pkt
        assert "metadata" in pkt

    def test_source_refs(self):
        pkt = self._assemble()
        refs = pkt["source_refs"]
        assert refs["enriched_candidate_ref"] == "art-enr-001"
        assert refs["policy_ref"] == "art-pol-001"
        assert refs["event_context_ref"] == "art-evt-001"
        assert refs["shared_context_ref"] == "art-ctx-001"

    def test_no_event_makes_degraded(self):
        pkt = self._assemble(event_ctx=None, event_artifact_ref=None)
        assert pkt["packet_status"] == PACKET_ASSEMBLED_DEGRADED
        assert pkt["source_refs"]["event_context_ref"] is None
        assert pkt["event_section"]["event_data_available"] is False
        assert (
            pkt["quality_section"]["section_statuses"]["event_section"]
            == SECTION_MISSING
        )

    def test_metadata_fields(self):
        pkt = self._assemble()
        meta = pkt["metadata"]
        assert meta["packet_version"] == _DECISION_PACKET_VERSION
        assert meta["stage_key"] == "orchestration"
        assert meta["policy_outcome"] == "eligible"
        assert meta["downstream_usable"] is True
        assert "assembly_timestamp" in meta

    def test_candidate_section_content(self):
        pkt = self._assemble()
        cs = pkt["candidate_section"]
        assert cs["candidate_id"] == "c1"
        assert cs["symbol"] == "SPY"
        assert cs["strategy_type"] == "put_credit_spread"
        assert cs["rank_score"] == 0.85

    def test_policy_section_authoritative(self):
        po = _make_policy_output(
            overall_outcome="blocked",
            blocking_reasons=["symbol overlap"],
            caution_reasons=["earnings nearby"],
        )
        pkt = self._assemble(policy_output=po)
        ps = pkt["policy_section"]
        assert ps["overall_outcome"] == "blocked"
        assert ps["blocking_reasons"] == ["symbol overlap"]
        assert ps["caution_reasons"] == ["earnings nearby"]

    def test_blocked_candidate_still_assembled(self):
        po = _make_policy_output(overall_outcome="blocked")
        pkt = self._assemble(policy_output=po)
        assert pkt["packet_status"] in (
            PACKET_ASSEMBLED, PACKET_ASSEMBLED_DEGRADED,
        )
        assert pkt["policy_section"]["overall_outcome"] == "blocked"

    def test_restricted_candidate_still_assembled(self):
        po = _make_policy_output(
            overall_outcome="restricted",
            restriction_reasons=["concentration too high"],
        )
        pkt = self._assemble(policy_output=po)
        assert pkt["policy_section"]["overall_outcome"] == "restricted"
        assert pkt["policy_section"]["restriction_reasons"] == [
            "concentration too high",
        ]


# =====================================================================
#  Handler contract tests
# =====================================================================

class TestHandlerContract:

    def test_return_shape(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")

        assert "outcome" in result
        assert "summary_counts" in result
        assert "artifacts" in result
        assert "metadata" in result
        assert "error" in result

    def test_summary_counts_keys(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        sc = result["summary_counts"]
        assert "total_assembled" in sc
        assert "total_degraded" in sc
        assert "total_failed" in sc


# =====================================================================
#  Single candidate
# =====================================================================

class TestSingleCandidate:

    def test_full_assembly(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_assembled"] == 1
        assert result["summary_counts"]["total_failed"] == 0
        assert len(result["artifacts"]) == 1

    def test_packet_artifact_retrievable(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(store, "orchestration", "decision_c1")
        assert art is not None
        pkt = art["data"]
        assert pkt["candidate_id"] == "c1"
        assert pkt["symbol"] == "SPY"
        assert "candidate_section" in pkt
        assert "policy_section" in pkt

    def test_no_events_still_succeeds(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["c1"], write_events=False,
        )
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"
        # degraded because event section missing
        assert result["summary_counts"]["total_degraded"] == 1

        art = get_artifact_by_key(store, "orchestration", "decision_c1")
        pkt = art["data"]
        assert pkt["packet_status"] == PACKET_ASSEMBLED_DEGRADED
        assert pkt["event_section"]["event_data_available"] is False


# =====================================================================
#  Multi-candidate
# =====================================================================

class TestMultipleCandidates:

    def test_two_candidates(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1", "c2"])
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_assembled"] == 2
        assert len(result["artifacts"]) == 2

        for cid in ("c1", "c2"):
            art = get_artifact_by_key(
                store, "orchestration", f"decision_{cid}",
            )
            assert art is not None

    def test_mixed_policy_outcomes(self):
        run, store = _make_run_and_store()
        po_blocked = _make_policy_output(
            candidate_id="c1",
            overall_outcome="blocked",
            blocking_reasons=["too many positions"],
        )
        po_eligible = _make_policy_output(
            candidate_id="c2",
            overall_outcome="eligible",
        )
        _populate_upstream(
            store, run["run_id"], ["c1", "c2"],
            policy_overrides={"c1": po_blocked, "c2": po_eligible},
        )
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_assembled"] == 2

        art1 = get_artifact_by_key(store, "orchestration", "decision_c1")
        assert art1["data"]["policy_section"]["overall_outcome"] == "blocked"

        art2 = get_artifact_by_key(store, "orchestration", "decision_c2")
        assert art2["data"]["policy_section"]["overall_outcome"] == "eligible"


# =====================================================================
#  Blocked / restricted candidate assembly
# =====================================================================

class TestBlockedCandidateAssembly:

    def test_blocked_candidate_produces_valid_packet(self):
        run, store = _make_run_and_store()
        po = _make_policy_output(
            candidate_id="c1",
            overall_outcome="blocked",
            blocking_reasons=["symbol overlap", "too many positions"],
            downstream_usable=True,
        )
        _populate_upstream(
            store, run["run_id"], ["c1"],
            policy_overrides={"c1": po},
        )
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_assembled"] == 1

        art = get_artifact_by_key(store, "orchestration", "decision_c1")
        pkt = art["data"]
        assert pkt["policy_section"]["overall_outcome"] == "blocked"
        assert pkt["policy_section"]["blocking_reasons"] == [
            "symbol overlap", "too many positions",
        ]

    def test_restricted_candidate_produces_valid_packet(self):
        run, store = _make_run_and_store()
        po = _make_policy_output(
            candidate_id="c1",
            overall_outcome="restricted",
            restriction_reasons=["high concentration"],
            downstream_usable=True,
        )
        _populate_upstream(
            store, run["run_id"], ["c1"],
            policy_overrides={"c1": po},
        )
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "orchestration", "decision_c1")
        pkt = art["data"]
        assert pkt["policy_section"]["overall_outcome"] == "restricted"


# =====================================================================
#  Vacuous completion
# =====================================================================

class TestVacuousCompletion:

    def test_no_enrichment_summary(self):
        run, store = _make_run_and_store()
        # Write policy summary but no enrichment summary
        _write_policy_summary(store, run["run_id"], [])
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_CANDIDATE_ENRICHMENT_SOURCE"

    def test_no_policy_summary(self):
        run, store = _make_run_and_store()
        # Write enrichment summary but no policy summary
        _write_enrichment_summary(store, run["run_id"], ["c1"])
        _write_enriched_candidate(store, run["run_id"], "c1")
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_POLICY_SOURCE"

    def test_zero_candidates(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], [])
        _write_policy_summary(store, run["run_id"], [])
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_assembled"] == 0

        # Summary artifact should exist
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        assert art is not None
        assert art["data"]["stage_status"] == "no_candidates_to_process"


# =====================================================================
#  Missing per-candidate artifacts
# =====================================================================

class TestMissingPerCandidateArtifacts:

    def test_missing_enriched_packet(self):
        run, store = _make_run_and_store()
        # Summary says c1 exists, but don't write the enriched artifact
        _write_enrichment_summary(store, run["run_id"], ["c1"])
        _write_policy_summary(store, run["run_id"], ["c1"])
        _write_policy_artifact(store, run["run_id"], "c1")
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["total_failed"] == 1

    def test_missing_policy_output(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], ["c1"])
        _write_enriched_candidate(store, run["run_id"], "c1")
        _write_policy_summary(store, run["run_id"], ["c1"])
        # Don't write per-candidate policy artifact
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["total_failed"] == 1


# =====================================================================
#  Partial failures
# =====================================================================

class TestPartialFailures:

    def test_one_missing_enriched_one_present(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], ["c1", "c2"])
        # Only write enriched for c2
        _write_enriched_candidate(store, run["run_id"], "c2")
        _write_policy_summary(store, run["run_id"], ["c1", "c2"])
        _write_policy_artifact(store, run["run_id"], "c1")
        _write_policy_artifact(store, run["run_id"], "c2")
        _write_event_context(store, run["run_id"], "c2")
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        # c2 succeeds, c1 fails → degraded
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_assembled"] == 1
        assert result["summary_counts"]["total_failed"] == 1

    def test_one_missing_policy_one_present(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], ["c1", "c2"])
        _write_enriched_candidate(store, run["run_id"], "c1")
        _write_enriched_candidate(store, run["run_id"], "c2")
        _write_policy_summary(store, run["run_id"], ["c1", "c2"])
        # Only write policy for c1
        _write_policy_artifact(store, run["run_id"], "c1")
        _write_event_context(store, run["run_id"], "c1")
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_assembled"] == 1
        assert result["summary_counts"]["total_failed"] == 1


# =====================================================================
#  Artifact creation and lineage
# =====================================================================

class TestArtifactWriting:

    def test_per_candidate_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(store, "orchestration", "decision_c1")

        assert art is not None
        assert art["artifact_type"] == "decision_packet"
        assert art["stage_key"] == "orchestration"
        assert art["candidate_id"] == "c1"

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        assert art is not None
        assert art["artifact_type"] == "decision_packet_summary"
        data = art["data"]
        assert data["total_assembled"] == 1
        assert len(data["output_artifact_refs"]) == 1

    def test_source_refs_in_packet(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(store, "orchestration", "decision_c1")
        refs = art["data"]["source_refs"]
        # All refs should be non-None (we wrote all upstream artifacts)
        assert refs["enriched_candidate_ref"] is not None
        assert refs["policy_ref"] is not None
        assert refs["event_context_ref"] is not None


# =====================================================================
#  Stage summary
# =====================================================================

class TestStageSummary:

    def test_summary_fields(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1", "c2"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        data = art["data"]
        assert data["stage_key"] == "orchestration"
        assert data["total_candidates_in"] == 2
        assert data["total_assembled"] == 2
        assert data["total_failed"] == 0
        assert len(data["candidate_ids_processed"]) == 2
        assert isinstance(data["section_availability"], dict)
        assert isinstance(data["policy_outcome_counts"], dict)
        assert isinstance(data["assembly_records"], list)
        assert "elapsed_ms" in data

    def test_summary_tracks_policy_outcomes(self):
        run, store = _make_run_and_store()
        po_blocked = _make_policy_output(
            candidate_id="c1", overall_outcome="blocked",
        )
        po_eligible = _make_policy_output(
            candidate_id="c2", overall_outcome="eligible",
        )
        _populate_upstream(
            store, run["run_id"], ["c1", "c2"],
            policy_overrides={"c1": po_blocked, "c2": po_eligible},
        )
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        counts = art["data"]["policy_outcome_counts"]
        assert counts.get("blocked") == 1
        assert counts.get("eligible") == 1


# =====================================================================
#  Event emission
# =====================================================================

class TestEventEmission:

    def test_started_and_completed_emitted(self):
        events = []
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(
            run, store, "orchestration",
            event_callback=events.append,
        )
        types = [e["event_type"] for e in events]
        assert "decision_packet_started" in types
        assert "decision_packet_completed" in types

    def test_failed_emitted_on_missing_enrichment(self):
        events = []
        run, store = _make_run_and_store()
        mark_stage_running(run, "orchestration")

        decision_packet_handler(
            run, store, "orchestration",
            event_callback=events.append,
        )
        types = [e["event_type"] for e in events]
        assert "decision_packet_started" in types
        assert "decision_packet_failed" in types

    def test_failed_emitted_on_missing_policy(self):
        events = []
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], ["c1"])
        _write_enriched_candidate(store, run["run_id"], "c1")
        mark_stage_running(run, "orchestration")

        decision_packet_handler(
            run, store, "orchestration",
            event_callback=events.append,
        )
        types = [e["event_type"] for e in events]
        assert "decision_packet_failed" in types

    def test_completed_metadata(self):
        events = []
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(
            run, store, "orchestration",
            event_callback=events.append,
        )
        completed = [
            e for e in events
            if e["event_type"] == "decision_packet_completed"
        ]
        assert len(completed) == 1
        meta = completed[0]["metadata"]
        assert meta["total_assembled"] == 1


# =====================================================================
#  Missing event context
# =====================================================================

class TestMissingEventContext:

    def test_missing_event_produces_degraded_packet(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["c1"], write_events=False,
        )
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        assert result["outcome"] == "completed"

        art = get_artifact_by_key(store, "orchestration", "decision_c1")
        pkt = art["data"]
        assert pkt["packet_status"] == PACKET_ASSEMBLED_DEGRADED
        assert pkt["event_section"]["event_data_available"] is False
        assert (
            pkt["quality_section"]["section_statuses"]["event_section"]
            == SECTION_MISSING
        )

    def test_missing_event_does_not_fail_stage(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["c1", "c2"], write_events=False,
        )
        mark_stage_running(run, "orchestration")

        result = decision_packet_handler(run, store, "orchestration")
        # Stage succeeds (degraded), not failed
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_assembled"] == 2


# =====================================================================
#  Orchestrator integration
# =====================================================================

class TestOrchestratorIntegration:

    def test_handler_wired_in_defaults(self):
        handlers = get_default_handlers()
        assert "orchestration" in handlers
        assert handlers["orchestration"] is decision_packet_handler

    def test_dependency_map(self):
        deps = get_default_dependency_map()
        assert "orchestration" in deps
        assert "candidate_enrichment" in deps["orchestration"]
        assert "policy" in deps["orchestration"]
        assert "events" in deps["orchestration"]

    def test_downstream_dependency(self):
        deps = get_default_dependency_map()
        assert "prompt_payload" in deps
        assert "orchestration" in deps["prompt_payload"]

    def test_pipeline_run_with_stubs(self):
        result = _all_stub_pipeline(run_id="run-orch-int-001")
        outcomes = {
            sr["stage_key"]: sr["outcome"]
            for sr in result["stage_results"]
        }
        assert outcomes["orchestration"] == "completed"

    def test_execute_stage_wrapper(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])

        result = execute_stage(
            run=run,
            artifact_store=store,
            stage_key="orchestration",
            handler=decision_packet_handler,
        )
        assert result["outcome"] == "completed"


# =====================================================================
#  Forward compatibility — prompt payload building
# =====================================================================

class TestForwardCompatibility:

    def test_packet_has_compact_downstream_fields(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(store, "orchestration", "decision_c1")
        pkt = art["data"]

        # These fields make downstream prompt/payload building easy
        assert "candidate_section" in pkt
        assert "event_section" in pkt
        assert "policy_section" in pkt
        assert "quality_section" in pkt
        assert "source_refs" in pkt
        assert pkt["metadata"]["downstream_usable"] is True

        # Policy summary is compact and structured
        ps = pkt["policy_section"]
        assert "overall_outcome" in ps
        assert "eligibility_flags" in ps
        assert "portfolio_context_summary" in ps

        # Event summary is compact
        es = pkt["event_section"]
        assert "event_data_available" in es
        assert "event_summary" in es

    def test_candidate_keyed_retrieval(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1", "c2"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")

        # Step 13 can iterate candidate_ids and fetch packets
        for cid in ("c1", "c2"):
            art = get_artifact_by_key(
                store, "orchestration", f"decision_{cid}",
            )
            assert art is not None
            assert art["data"]["candidate_id"] == cid

    def test_summary_has_candidate_refs(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1", "c2"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        data = art["data"]
        # Step 13 can look up candidate IDs and artifact refs
        assert "c1" in data["output_artifact_refs"]
        assert "c2" in data["output_artifact_refs"]
        assert set(data["candidate_ids_processed"]) == {"c1", "c2"}


# =====================================================================
#  Section availability tracking
# =====================================================================

class TestSectionAvailability:

    def test_all_sections_present(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        sa = art["data"]["section_availability"]
        assert sa["candidate_present"] == 1
        assert sa["event_present"] == 1
        assert sa["policy_present"] == 1
        assert sa["event_missing"] == 0

    def test_event_missing_tracked(self):
        run, store = _make_run_and_store()
        _populate_upstream(
            store, run["run_id"], ["c1"], write_events=False,
        )
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        sa = art["data"]["section_availability"]
        assert sa["event_missing"] == 1
        assert sa["event_present"] == 0


# =====================================================================
#  Assembly record structure
# =====================================================================

class TestAssemblyRecords:

    def test_record_fields(self):
        run, store = _make_run_and_store()
        _populate_upstream(store, run["run_id"], ["c1"])
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        records = art["data"]["assembly_records"]
        assert len(records) == 1
        r = records[0]
        assert r["candidate_id"] == "c1"
        assert r["symbol"] == "SPY"
        assert r["packet_status"] == PACKET_ASSEMBLED
        assert r["source_enriched_candidate_ref"] is not None
        assert r["source_policy_ref"] is not None
        assert r["source_event_ref"] is not None
        assert "candidate_section" in r["included_sections"]
        assert "policy_section" in r["included_sections"]
        assert "event_section" in r["included_sections"]
        assert r["missing_sections"] == []
        assert r["downstream_usable"] is True
        assert r["output_artifact_ref"] is not None
        assert isinstance(r["elapsed_ms"], int)
        assert r["error"] is None

    def test_failed_record(self):
        run, store = _make_run_and_store()
        _write_enrichment_summary(store, run["run_id"], ["c1"])
        _write_policy_summary(store, run["run_id"], ["c1"])
        _write_policy_artifact(store, run["run_id"], "c1")
        # No enriched packet for c1
        mark_stage_running(run, "orchestration")

        decision_packet_handler(run, store, "orchestration")
        art = get_artifact_by_key(
            store, "orchestration", "decision_packet_summary",
        )
        records = art["data"]["assembly_records"]
        assert len(records) == 1
        r = records[0]
        assert r["packet_status"] == PACKET_FAILED
        assert r["downstream_usable"] is False
        assert r["error"] is not None
        assert r["error"]["code"] == "ENRICHED_PACKET_MISSING"
