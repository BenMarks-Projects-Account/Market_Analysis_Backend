"""Tests for pipeline_final_recommendation_stage — Step 14.

Covers:
  - Status vocabulary/constant registration
  - Default model executor behavior
  - Response normalization (full, degraded, error, missing fields)
  - Policy guardrail echo preservation
  - Guardrail consistency warnings
  - Raw response excerpt building
  - Handler contract (return shape, summary_counts, metadata)
  - Prompt payload retrieval from Step 13
  - Missing prompt payload summary
  - Empty candidate set
  - Zero runnable payloads (downstream_usable=false)
  - Skipped_not_runnable path
  - Successful execution path (single and multi candidate)
  - Degraded execution path
  - Partial candidate failures
  - All runnable executions fail
  - Injectable executor behavior
  - Input mode selection (structured, text)
  - Override metadata capture
  - Sequential execution queue (order, one-active, duplicates, progress)
  - Incremental artifact writing (per-candidate, not batched)
  - Per-candidate event emission (started/completed ordering)
  - Artifact creation and lineage
  - Stage summary contents
  - Provider/model metadata capture
  - Execution record structure
  - Event emission (started/completed/failed)
  - Orchestrator integration (wiring, deps, pipeline run)
  - Forward compatibility for response normalization stage
"""

import pytest

from app.services.pipeline_final_recommendation_stage import (
    _STAGE_KEY,
    _FINAL_RECOMMENDATION_VERSION,
    STATUS_COMPLETED,
    STATUS_COMPLETED_DEGRADED,
    STATUS_SKIPPED_NOT_RUNNABLE,
    STATUS_FAILED,
    VALID_FINAL_STATUSES,
    default_model_executor,
    normalize_model_response,
    final_recommendation_handler,
    ModelExecutor,
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

@pytest.fixture(autouse=True)
def _use_stub_executor(monkeypatch):
    """Default to stub executor in tests — no LLM server required.

    The live handler defaults to ``real_model_executor``.  This fixture
    replaces the module-level ``real_model_executor`` with the stub so
    that tests not passing an explicit ``model_executor`` kwarg do not
    attempt real HTTP calls.  Tests that supply their own executor
    (``_custom_executor``, ``_failing_executor``, etc.) are unaffected.
    """
    import app.services.pipeline_final_recommendation_stage as mod
    monkeypatch.setattr(mod, "real_model_executor", mod.stub_model_executor)


def _make_run_and_store(run_id="test-fm-001"):
    """Create a fresh run+store with upstream stages completed.

    Completes through prompt_payload (since prompt_payload runs
    before final_model_decision in the canonical stage order).
    """
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    for stage in (
        "market_data", "market_model_analysis",
        "stock_scanners", "options_scanners", "candidate_selection",
        "shared_context", "candidate_enrichment",
        "events", "policy", "orchestration",
        "prompt_payload",
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
    handlers.setdefault("stock_scanners", _success_handler)
    handlers.setdefault("options_scanners", _success_handler)
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


# ── Prompt payload builders (Step 13 output shapes) ─────────────

def _make_prompt_payload(
    candidate_id="c1",
    symbol="SPY",
    *,
    downstream_usable=True,
    payload_status="built",
    overall_outcome="eligible",
    blocking_reasons=None,
    caution_reasons=None,
    restriction_reasons=None,
    degraded_reasons=None,
    run_id="test-fm-001",
):
    """Build a minimal prompt payload as Step 13 produces."""
    return {
        "prompt_payload_version": "1.0",
        "run_id": run_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "payload_status": payload_status,
        "source_decision_packet_ref": f"art-dp-{candidate_id}",
        "compact_candidate_block": {
            "candidate_id": candidate_id,
            "symbol": symbol,
            "strategy_type": "put_credit_spread",
            "direction": "long",
            "rank_position": 1,
            "rank_score": 0.85,
            "setup_quality": 75.0,
            "confidence": 0.8,
        },
        "compact_event_block": {
            "event_data_available": True,
            "event_status": "enriched",
            "nearest_event_type": "earnings",
            "nearest_days_until": 9,
            "risk_flags": [],
        },
        "compact_policy_block": {
            "overall_outcome": overall_outcome,
            "policy_status": "evaluated",
            "downstream_usable": downstream_usable,
            "blocking_reasons": blocking_reasons or [],
            "caution_reasons": caution_reasons or [],
            "restriction_reasons": restriction_reasons or [],
            "eligibility_flags": {"trade_capable": True},
            "check_summary": {"total": 2, "passed": 2, "failed": 0},
            "failing_checks": [],
        },
        "compact_quality_block": {
            "section_statuses": {
                "candidate_section": "present",
                "event_section": "present",
                "policy_section": "present",
            },
            "missing_sections": [],
            "degraded_sections": [],
            "degraded_reasons": [],
            "downstream_usable": downstream_usable,
        },
        "rendered_prompt_text": f"CANDIDATE: {symbol} | put_credit_spread | long",
        "compression_metadata": {
            "sections_compressed": [
                "candidate_section", "event_section",
                "policy_section", "quality_section",
            ],
            "trimmed_fields": [],
            "payload_version": "1.0",
        },
        "source_section_refs": {
            "decision_packet_ref": f"art-dp-{candidate_id}",
            "decision_packet_version": "1.0",
        },
        "downstream_usable": downstream_usable,
        "warnings": [],
        "degraded_reasons": degraded_reasons or [],
        "metadata": {
            "assembly_timestamp": "2026-03-11T12:00:00+00:00",
            "payload_version": "1.0",
            "stage_key": "prompt_payload",
            "policy_outcome": overall_outcome,
            "policy_status": "evaluated",
            "downstream_usable": downstream_usable,
        },
    }


def _write_prompt_payload(
    store, run_id, candidate_id, payload=None, **overrides,
):
    """Write a Step 13 per-candidate prompt payload artifact."""
    if payload is None:
        payload = _make_prompt_payload(
            candidate_id=candidate_id,
            run_id=run_id,
            **overrides,
        )
    art = build_artifact_record(
        run_id=run_id,
        stage_key="prompt_payload",
        artifact_key=f"prompt_{candidate_id}",
        artifact_type="prompt_payload",
        data=payload,
        candidate_id=candidate_id,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _write_prompt_payload_summary(
    store, run_id, candidate_ids, **overrides,
):
    """Write a Step 13 prompt_payload_summary artifact."""
    records = [
        {
            "candidate_id": cid,
            "payload_status": "built",
            "downstream_usable": overrides.get("downstream_usable", True),
        }
        for cid in candidate_ids
    ]
    data = {
        "stage_key": "prompt_payload",
        "stage_status": "success",
        "total_candidates_in": len(candidate_ids),
        "total_built": len(candidate_ids),
        "total_degraded": 0,
        "total_failed": 0,
        "candidate_ids_processed": list(candidate_ids),
        "output_artifact_refs": {
            cid: f"art-pp-{cid}" for cid in candidate_ids
        },
        "payload_records": records,
        "warnings": [],
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="prompt_payload",
        artifact_key="prompt_payload_summary",
        artifact_type="prompt_payload_summary",
        data=data,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _populate_prompt_payloads(
    store, run_id, candidate_ids, **overrides,
):
    """Write summary + per-candidate payloads for given candidate IDs."""
    _write_prompt_payload_summary(store, run_id, candidate_ids, **overrides)
    for cid in candidate_ids:
        _write_prompt_payload(store, run_id, cid, **overrides)


# ── Custom executors for testing ────────────────────────────────

def _failing_executor(payload, rendered_text):
    """Executor that always raises."""
    raise RuntimeError("Model execution intentionally failed")


def _error_status_executor(payload, rendered_text):
    """Executor that returns error status."""
    return {
        "status": "error",
        "raw_response": {},
        "provider": "test_provider",
        "model_name": "test_model",
        "latency_ms": 5,
        "metadata": {"error": "test error"},
        "error": "test_model_error",
    }


def _partial_executor(payload, rendered_text):
    """Executor that returns success but missing decision."""
    return {
        "status": "success",
        "raw_response": {
            "rationale_summary": "Partial response",
        },
        "provider": "test_provider",
        "model_name": "test_model",
        "latency_ms": 10,
        "metadata": {},
    }


def _custom_executor(payload, rendered_text):
    """Executor that returns a full custom response."""
    return {
        "status": "success",
        "raw_response": {
            "decision": "buy",
            "conviction": 0.9,
            "rationale_summary": "Strong setup with favorable conditions",
            "key_supporting_points": ["bullish trend", "high volume"],
            "key_risks": ["earnings in 9 days"],
            "market_alignment": "bullish",
            "portfolio_fit": "good",
            "event_sensitivity": "moderate",
            "sizing_guidance": "standard",
        },
        "provider": "custom_provider",
        "model_name": "custom_model_v2",
        "latency_ms": 42,
        "metadata": {"custom_key": "custom_value"},
    }


# =====================================================================
#  TestConstants
# =====================================================================

class TestConstants:
    def test_stage_key(self):
        assert _STAGE_KEY == "final_model_decision"
        assert _STAGE_KEY in PIPELINE_STAGES

    def test_version(self):
        assert _FINAL_RECOMMENDATION_VERSION == "1.0"

    def test_valid_final_statuses(self):
        assert STATUS_COMPLETED in VALID_FINAL_STATUSES
        assert STATUS_COMPLETED_DEGRADED in VALID_FINAL_STATUSES
        assert STATUS_SKIPPED_NOT_RUNNABLE in VALID_FINAL_STATUSES
        assert STATUS_FAILED in VALID_FINAL_STATUSES

    def test_event_types_registered(self):
        assert "final_model_started" in VALID_EVENT_TYPES
        assert "final_model_completed" in VALID_EVENT_TYPES
        assert "final_model_failed" in VALID_EVENT_TYPES

    def test_artifact_types_registered(self):
        assert "final_model_output" in VALID_ARTIFACT_TYPES
        assert "final_model_summary" in VALID_ARTIFACT_TYPES


# =====================================================================
#  TestDefaultModelExecutor
# =====================================================================

class TestDefaultModelExecutor:
    def test_eligible_candidate_gets_buy(self):
        payload = _make_prompt_payload(overall_outcome="eligible")
        result = default_model_executor(payload, None)
        assert result["status"] == "success"
        assert result["raw_response"]["decision"] == "buy"
        assert result["raw_response"]["conviction"] == 0.7
        assert result["provider"] == "stub"

    def test_blocked_candidate_gets_pass(self):
        payload = _make_prompt_payload(overall_outcome="blocked")
        result = default_model_executor(payload, None)
        assert result["raw_response"]["decision"] == "pass"
        assert result["raw_response"]["conviction"] == 0.0

    def test_restricted_candidate_gets_pass(self):
        payload = _make_prompt_payload(overall_outcome="restricted")
        result = default_model_executor(payload, None)
        assert result["raw_response"]["decision"] == "pass"
        assert result["raw_response"]["conviction"] == 0.1

    def test_caution_candidate_gets_hold(self):
        payload = _make_prompt_payload(overall_outcome="caution")
        result = default_model_executor(payload, None)
        assert result["raw_response"]["decision"] == "hold"
        assert result["raw_response"]["conviction"] == 0.4

    def test_result_shape(self):
        payload = _make_prompt_payload()
        result = default_model_executor(payload, None)
        assert "status" in result
        assert "raw_response" in result
        assert "provider" in result
        assert "model_name" in result
        assert "latency_ms" in result
        assert "metadata" in result


# =====================================================================
#  TestNormalizeModelResponse
# =====================================================================

class TestNormalizeModelResponse:
    def _raw_success(self, **overrides):
        base = {
            "status": "success",
            "raw_response": {
                "decision": "buy",
                "conviction": 0.8,
                "rationale_summary": "Looks good",
                "key_supporting_points": ["point1"],
                "key_risks": ["risk1"],
                "market_alignment": "bullish",
                "portfolio_fit": "good",
                "event_sensitivity": "low",
                "sizing_guidance": "standard",
            },
            "provider": "test",
            "model_name": "test_model",
            "latency_ms": 10,
            "metadata": {},
        }
        base.update(overrides)
        return base

    def test_successful_normalization(self):
        payload = _make_prompt_payload()
        raw = self._raw_success()
        norm = normalize_model_response(raw, payload, "run-1")

        assert norm["final_recommendation_version"] == "1.0"
        assert norm["run_id"] == "run-1"
        assert norm["candidate_id"] == "c1"
        assert norm["symbol"] == "SPY"
        assert norm["final_status"] == STATUS_COMPLETED
        assert norm["model_execution_status"] == "success"
        assert norm["recommendation"]["decision"] == "buy"
        assert norm["recommendation"]["conviction"] == 0.8
        assert norm["quality"]["response_quality"] == "full"
        assert norm["quality"]["downstream_usable"] is True

    def test_error_status_normalization(self):
        payload = _make_prompt_payload()
        raw = {
            "status": "error",
            "raw_response": {},
            "provider": "test",
            "model_name": "test_model",
            "latency_ms": 5,
            "metadata": {},
            "error": "timeout",
        }
        norm = normalize_model_response(raw, payload, "run-1")

        assert norm["final_status"] == STATUS_FAILED
        assert norm["model_execution_status"] == "error"
        assert norm["quality"]["response_quality"] == "degraded"
        assert norm["quality"]["downstream_usable"] is False
        assert "timeout" in norm["quality"]["degraded_reasons"]

    def test_partial_response_normalization(self):
        payload = _make_prompt_payload()
        raw = self._raw_success()
        raw["raw_response"] = {"rationale_summary": "partial"}
        norm = normalize_model_response(raw, payload, "run-1")

        assert norm["model_execution_status"] == "success_partial"
        assert norm["final_status"] == STATUS_COMPLETED_DEGRADED
        assert "missing_decision_in_response" in (
            norm["quality"]["degraded_reasons"]
        )
        assert norm["quality"]["downstream_usable"] is True

    def test_policy_guardrail_echo(self):
        payload = _make_prompt_payload(
            overall_outcome="caution",
            caution_reasons=["near earnings"],
        )
        raw = self._raw_success()
        norm = normalize_model_response(raw, payload, "run-1")

        echo = norm["policy_guardrail_echo"]
        assert echo["overall_outcome"] == "caution"
        assert echo["cautions"] == ["near earnings"]
        assert echo["blockers"] == []
        assert echo["restrictions"] == []

    def test_blocked_guardrail_echo(self):
        payload = _make_prompt_payload(
            overall_outcome="blocked",
            blocking_reasons=["capital limit exceeded"],
        )
        raw = self._raw_success()
        norm = normalize_model_response(raw, payload, "run-1")

        echo = norm["policy_guardrail_echo"]
        assert echo["overall_outcome"] == "blocked"
        assert echo["blockers"] == ["capital limit exceeded"]

    def test_guardrail_consistency_warning_blocked(self):
        payload = _make_prompt_payload(overall_outcome="blocked")
        raw = self._raw_success()
        raw["raw_response"]["decision"] = "buy"
        norm = normalize_model_response(raw, payload, "run-1")

        assert any("blocked" in w for w in norm["warnings"])

    def test_guardrail_consistency_warning_restricted(self):
        payload = _make_prompt_payload(overall_outcome="restricted")
        raw = self._raw_success()
        raw["raw_response"]["decision"] = "buy"
        norm = normalize_model_response(raw, payload, "run-1")

        assert any("restricted" in w for w in norm["warnings"])

    def test_no_warning_when_blocked_pass(self):
        payload = _make_prompt_payload(overall_outcome="blocked")
        raw = self._raw_success()
        raw["raw_response"]["decision"] = "pass"
        norm = normalize_model_response(raw, payload, "run-1")

        assert not any("blocked" in w for w in norm["warnings"])

    def test_model_metadata_capture(self):
        payload = _make_prompt_payload()
        raw = self._raw_success(
            provider="bedrock",
            model_name="claude-opus",
            latency_ms=150,
        )
        raw["override_used"] = True
        raw["routing_metadata"] = {"region": "us-east-1"}
        raw["input_mode"] = "text"
        norm = normalize_model_response(raw, payload, "run-1")

        meta = norm["model_metadata"]
        assert meta["provider"] == "bedrock"
        assert meta["model_name"] == "claude-opus"
        assert meta["latency_ms"] == 150
        assert meta["override_used"] is True
        assert meta["routing_metadata"] == {"region": "us-east-1"}
        assert meta["input_mode"] == "text"

    def test_raw_response_excerpt(self):
        payload = _make_prompt_payload()
        raw = self._raw_success()
        norm = normalize_model_response(raw, payload, "run-1")

        excerpt = norm["raw_response_excerpt"]
        assert "keys_present" in excerpt
        assert "decision" in excerpt
        assert "conviction" in excerpt

    def test_non_dict_raw_response(self):
        payload = _make_prompt_payload()
        raw = {"status": "success", "raw_response": "not a dict"}
        norm = normalize_model_response(raw, payload, "run-1")
        # Should not crash; raw_body treated as empty dict
        assert norm["recommendation"]["decision"] is None

    def test_payload_degraded_reasons_carried(self):
        payload = _make_prompt_payload(
            degraded_reasons=["event section degraded"],
        )
        raw = self._raw_success()
        norm = normalize_model_response(raw, payload, "run-1")

        assert "event section degraded" in (
            norm["quality"]["degraded_reasons"]
        )
        assert norm["final_status"] == STATUS_COMPLETED_DEGRADED

    def test_source_prompt_payload_ref(self):
        payload = _make_prompt_payload()
        raw = self._raw_success()
        norm = normalize_model_response(raw, payload, "run-1")

        assert norm["source_prompt_payload_ref"] == "art-dp-c1"

    def test_metadata_fields(self):
        payload = _make_prompt_payload()
        raw = self._raw_success()
        norm = normalize_model_response(raw, payload, "run-1")

        assert "normalization_timestamp" in norm["metadata"]
        assert norm["metadata"]["stage_key"] == "final_model_decision"
        assert norm["metadata"]["recommendation_version"] == "1.0"


# =====================================================================
#  TestHandlerContract
# =====================================================================

class TestHandlerContract:
    """Verify handler return shape matches orchestrator expectations."""

    def test_handler_returns_dict(self):
        run, store = _make_run_and_store()
        _populate_prompt_payloads(store, run["run_id"], ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert isinstance(result, dict)

    def test_handler_has_required_keys(self):
        run, store = _make_run_and_store()
        _populate_prompt_payloads(store, run["run_id"], ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        for key in ("outcome", "summary_counts", "artifacts",
                     "metadata", "error"):
            assert key in result, f"missing key: {key}"

    def test_outcome_is_completed_on_success(self):
        run, store = _make_run_and_store()
        _populate_prompt_payloads(store, run["run_id"], ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        assert result["error"] is None


# =====================================================================
#  TestMissingPromptPayloadSummary
# =====================================================================

class TestMissingPromptPayloadSummary:
    def test_fails_with_no_prompt_payload_source(self):
        run, store = _make_run_and_store()
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_PROMPT_PAYLOAD_SOURCE"


# =====================================================================
#  TestVacuousCompletion
# =====================================================================

class TestVacuousCompletion:
    def test_zero_candidates(self):
        run, store = _make_run_and_store()
        _write_prompt_payload_summary(store, run["run_id"], [])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        assert result["metadata"]["stage_status"] == "no_candidates_to_process"

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        _write_prompt_payload_summary(store, run["run_id"], [])
        final_recommendation_handler(run, store, "final_model_decision")
        art = get_artifact_by_key(
            store, "final_model_decision", "final_model_summary",
        )
        assert art is not None


# =====================================================================
#  TestSkippedNotRunnable
# =====================================================================

class TestSkippedNotRunnable:
    def test_non_usable_payload_skipped(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1"])
        _write_prompt_payload(
            store, rid, "c1", downstream_usable=False,
        )
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        assert result["metadata"]["stage_status"] == "no_runnable_candidates"

    def test_skipped_recorded_in_summary(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1"])
        _write_prompt_payload(
            store, rid, "c1", downstream_usable=False,
        )
        final_recommendation_handler(run, store, "final_model_decision")
        art = get_artifact_by_key(
            store, "final_model_decision", "final_model_summary",
        )
        summary = art["data"]
        assert summary["total_skipped"] == 0  # vacuous path handles this
        assert "no_runnable_candidates" in summary["stage_status"]

    def test_mixed_runnable_and_skipped(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1", "c2"])
        _write_prompt_payload(
            store, rid, "c1", downstream_usable=True,
        )
        _write_prompt_payload(
            store, rid, "c2", downstream_usable=False,
        )
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        summary = result["metadata"]["stage_summary"]
        assert summary["total_runnable"] == 1
        assert summary["total_skipped"] == 1


# =====================================================================
#  TestSingleCandidate
# =====================================================================

class TestSingleCandidate:
    def test_successful_execution(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_completed"] == 1
        assert result["summary_counts"]["total_failed"] == 0

    def test_per_candidate_artifact_written(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        assert art is not None
        data = art["data"]
        assert data["candidate_id"] == "c1"
        assert data["final_status"] == STATUS_COMPLETED
        assert data["recommendation"]["decision"] is not None

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_model_summary",
        )
        assert art is not None
        data = art["data"]
        assert data["total_completed"] == 1


# =====================================================================
#  TestMultipleCandidates
# =====================================================================

class TestMultipleCandidates:
    def test_multiple_candidates_complete(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2", "c3"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_completed"] == 3

    def test_each_has_artifact(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2"])
        final_recommendation_handler(run, store, "final_model_decision")

        for cid in ["c1", "c2"]:
            art = get_artifact_by_key(
                store, "final_model_decision", f"final_{cid}",
            )
            assert art is not None
            assert art["data"]["candidate_id"] == cid


# =====================================================================
#  TestInjectableExecutor
# =====================================================================

class TestInjectableExecutor:
    def test_custom_executor_used(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_custom_executor,
        )
        assert result["outcome"] == "completed"
        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        assert data["recommendation"]["decision"] == "buy"
        assert data["recommendation"]["conviction"] == 0.9
        assert data["model_metadata"]["provider"] == "custom_provider"
        assert data["model_metadata"]["model_name"] == "custom_model_v2"

    def test_failing_executor(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_failing_executor,
        )
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "FINAL_MODEL_ALL_FAILED"

    def test_error_status_executor(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_error_status_executor,
        )
        assert result["outcome"] == "failed"

    def test_partial_executor(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_partial_executor,
        )
        assert result["outcome"] == "completed"
        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        assert data["final_status"] == STATUS_COMPLETED_DEGRADED
        assert data["model_execution_status"] == "success_partial"


# =====================================================================
#  TestDegradedExecution
# =====================================================================

class TestDegradedExecution:
    def test_degraded_payload_still_runs(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1"])
        _write_prompt_payload(
            store, rid, "c1",
            payload_status="built_degraded",
            degraded_reasons=["event section degraded"],
        )
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        assert data["final_status"] == STATUS_COMPLETED_DEGRADED
        assert "event section degraded" in (
            data["quality"]["degraded_reasons"]
        )


# =====================================================================
#  TestPartialFailures
# =====================================================================

class TestPartialFailures:
    def test_one_fails_others_complete(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1", "c2"])
        _write_prompt_payload(store, rid, "c1")
        _write_prompt_payload(store, rid, "c2")

        call_count = {"n": 0}

        def _first_fail_executor(payload, text):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("First call fails")
            return default_model_executor(payload, text)

        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_first_fail_executor,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_completed"] == 1
        assert result["summary_counts"]["total_failed"] == 1
        summary = result["metadata"]["stage_summary"]
        assert summary["stage_status"] == "degraded"

    def test_missing_per_candidate_payload(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1", "c2"])
        # Only write c1, c2 is missing
        _write_prompt_payload(store, rid, "c1")
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        # c2 should be failed (missing payload), c1 completed
        summary = result["metadata"]["stage_summary"]
        assert summary["total_completed"] == 1


# =====================================================================
#  TestAllFailed
# =====================================================================

class TestAllFailed:
    def test_all_executions_fail(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_failing_executor,
        )
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "FINAL_MODEL_ALL_FAILED"

    def test_summary_still_written(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_failing_executor,
        )
        art = get_artifact_by_key(
            store, "final_model_decision", "final_model_summary",
        )
        assert art is not None
        assert art["data"]["stage_status"] == "failed"


# =====================================================================
#  TestBlockedCandidateExecution
# =====================================================================

class TestBlockedCandidateExecution:
    def test_blocked_but_usable_runs(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1"])
        _write_prompt_payload(
            store, rid, "c1",
            overall_outcome="blocked",
            blocking_reasons=["capital exceeded"],
            downstream_usable=True,
        )
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        echo = data["policy_guardrail_echo"]
        assert echo["overall_outcome"] == "blocked"
        assert "capital exceeded" in echo["blockers"]

    def test_blocked_not_usable_skipped(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1"])
        _write_prompt_payload(
            store, rid, "c1",
            overall_outcome="blocked",
            downstream_usable=False,
        )
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        assert result["metadata"]["stage_status"] == "no_runnable_candidates"


# =====================================================================
#  TestInputModeSelection
# =====================================================================

class TestInputModeSelection:
    def test_structured_mode_default(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])

        captured = {}

        def _capture_executor(payload, text):
            captured["payload"] = payload
            captured["text"] = text
            return default_model_executor(payload, text)

        final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_capture_executor,
        )
        assert "payload" in captured
        assert captured["text"] is not None  # rendered_prompt_text exists

    def test_text_mode_explicit(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])

        final_recommendation_handler(
            run, store, "final_model_decision",
            input_mode="text",
        )
        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        # The input_mode should be recorded in model_metadata
        data = art["data"]
        assert data["model_metadata"]["input_mode"] == "text"


# =====================================================================
#  TestOverrideMetadata
# =====================================================================

class TestOverrideMetadata:
    def test_override_recorded(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(
            run, store, "final_model_decision",
            override_used=True,
        )
        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        assert data["model_metadata"]["override_used"] is True

    def test_no_override_default(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(
            run, store, "final_model_decision",
        )
        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        assert data["model_metadata"]["override_used"] is False


# =====================================================================
#  TestBoundedParallelExecution
# =====================================================================

class TestSequentialExecutionQueue:
    """Verify the sequential execution queue contract."""

    def test_sequential_produces_same_results(self):
        """Three candidates run one-at-a-time and all complete."""
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2", "c3"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_completed"] == 3

    def test_sequential_with_failure(self):
        """One failure does not prevent remaining candidates."""
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2"])

        def _fail_first(payload, text):
            cid = payload.get("candidate_id")
            if cid == "c1":
                raise RuntimeError("c1 fails")
            return default_model_executor(payload, text)

        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_fail_first,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_completed"] == 1
        assert result["summary_counts"]["total_failed"] == 1

    def test_strictly_sequential_order(self):
        """Candidates execute in the order they appear in the queue."""
        run, store = _make_run_and_store()
        rid = run["run_id"]
        ids = ["cA", "cB", "cC"]
        _populate_prompt_payloads(store, rid, ids)

        execution_order = []

        def _tracking_executor(payload, text):
            execution_order.append(payload.get("candidate_id"))
            return default_model_executor(payload, text)

        final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_tracking_executor,
        )
        assert execution_order == ids

    def test_one_active_call_at_a_time(self):
        """Only one executor call is active at any moment."""
        import time as _time

        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2", "c3"])

        active_count = {"current": 0, "max_seen": 0}

        def _concurrency_detector(payload, text):
            active_count["current"] += 1
            if active_count["current"] > active_count["max_seen"]:
                active_count["max_seen"] = active_count["current"]
            _time.sleep(0.01)  # small delay to expose concurrency
            result = default_model_executor(payload, text)
            active_count["current"] -= 1
            return result

        final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_concurrency_detector,
        )
        assert active_count["max_seen"] == 1, (
            f"Expected max 1 active call, saw {active_count['max_seen']}"
        )

    def test_duplicate_candidate_id_prevented(self):
        """Duplicate candidate IDs in the queue are skipped."""
        run, store = _make_run_and_store()
        rid = run["run_id"]
        # Write summary with duplicated c1
        _write_prompt_payload_summary(store, rid, ["c1", "c1", "c2"])
        _write_prompt_payload(store, rid, "c1")
        _write_prompt_payload(store, rid, "c2")

        call_ids = []

        def _tracking(payload, text):
            call_ids.append(payload.get("candidate_id"))
            return default_model_executor(payload, text)

        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_tracking,
        )
        # c1 should only be executed once
        assert call_ids.count("c1") == 1
        assert result["outcome"] == "completed"

    def test_per_candidate_progress_callback(self):
        """progress_callback receives a snapshot after each candidate."""
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2"])

        progress_snapshots = []

        def _progress_cb(progress):
            progress_snapshots.append(progress)

        final_recommendation_handler(
            run, store, "final_model_decision",
            progress_callback=_progress_cb,
        )
        assert len(progress_snapshots) == 2

        # First candidate progress
        p1 = progress_snapshots[0]
        assert p1["queue_position"] == 1
        assert p1["current_candidate_id"] == "c1"
        assert p1["completed_count"] == 1
        assert p1["remaining_count"] == 1
        assert p1["total_runnable"] == 2

        # Second candidate progress
        p2 = progress_snapshots[1]
        assert p2["queue_position"] == 2
        assert p2["current_candidate_id"] == "c2"
        assert p2["completed_count"] == 2
        assert p2["remaining_count"] == 0

    def test_incremental_artifact_writing(self):
        """Each candidate's artifact is written immediately, not batched."""
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2", "c3"])

        artifacts_after_each = []

        def _artifact_checking_executor(payload, text):
            # Check how many final_* artifacts exist RIGHT NOW
            # using the artifact_index which keys by "stage::artifact_key"
            cid = payload.get("candidate_id")
            index = store.get("artifact_index", {})
            count = sum(
                1 for key in index
                if key.startswith("final_model_decision::final_c")
            )
            artifacts_after_each.append(
                {"before_cid": cid, "existing_count": count}
            )
            return default_model_executor(payload, text)

        final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_artifact_checking_executor,
        )
        # Before executing c1, 0 artifacts; before c2, 1; before c3, 2
        assert artifacts_after_each[0]["existing_count"] == 0
        assert artifacts_after_each[1]["existing_count"] == 1
        assert artifacts_after_each[2]["existing_count"] == 2

    def test_candidate_events_emitted(self):
        """Started/completed events emitted for each candidate."""
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2"])

        events = []
        final_recommendation_handler(
            run, store, "final_model_decision",
            event_callback=lambda e: events.append(e),
        )
        types = [e["event_type"] for e in events]
        assert types.count("candidate_execution_started") == 2
        assert types.count("candidate_execution_completed") == 2

        # Verify ordering: started before completed for each
        started_indices = [
            i for i, e in enumerate(events)
            if e["event_type"] == "candidate_execution_started"
        ]
        completed_indices = [
            i for i, e in enumerate(events)
            if e["event_type"] == "candidate_execution_completed"
        ]
        # First candidate started before first completed
        assert started_indices[0] < completed_indices[0]
        # Second candidate started after first completed (sequential!)
        assert started_indices[1] > completed_indices[0]


# =====================================================================
#  TestArtifactWriting
# =====================================================================

class TestArtifactWriting:
    def test_per_candidate_artifact_type(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        assert art["artifact_type"] == "final_model_output"
        assert art["candidate_id"] == "c1"
        assert art["stage_key"] == "final_model_decision"

    def test_summary_artifact_type(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_model_summary",
        )
        assert art["artifact_type"] == "final_model_summary"
        assert art["stage_key"] == "final_model_decision"

    def test_artifact_summary_has_decision(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        assert "decision" in art["summary"]
        assert "conviction" in art["summary"]
        assert "final_status" in art["summary"]

    def test_output_artifact_refs_in_summary(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_model_summary",
        )
        refs = art["data"]["output_artifact_refs"]
        assert "c1" in refs
        assert "c2" in refs


# =====================================================================
#  TestStageSummary
# =====================================================================

class TestStageSummary:
    def test_summary_fields(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        summary = result["metadata"]["stage_summary"]

        assert summary["stage_key"] == "final_model_decision"
        assert summary["total_candidates_loaded"] == 2
        assert summary["total_runnable"] == 2
        assert summary["total_completed"] == 2
        assert summary["total_skipped"] == 0
        assert summary["total_failed"] == 0
        assert "c1" in summary["candidate_ids_processed"]
        assert "c2" in summary["candidate_ids_processed"]
        assert isinstance(summary["provider_usage_counts"], dict)
        assert isinstance(summary["execution_records"], list)
        assert summary["elapsed_ms"] >= 0

    def test_provider_usage_tracked(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=default_model_executor,
        )
        summary = result["metadata"]["stage_summary"]
        assert "stub" in summary["provider_usage_counts"]
        assert summary["provider_usage_counts"]["stub"] == 1


# =====================================================================
#  TestExecutionRecords
# =====================================================================

class TestExecutionRecords:
    def test_record_fields(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        records = result["metadata"]["stage_summary"]["execution_records"]
        assert len(records) >= 1
        rec = records[0]

        for key in (
            "candidate_id", "symbol", "payload_status",
            "execution_status", "source_prompt_payload_ref",
            "provider", "model_name", "input_mode_used",
            "override_used", "output_artifact_ref",
            "downstream_usable", "degraded_reasons", "elapsed_ms",
        ):
            assert key in rec, f"missing record key: {key}"

    def test_skipped_record_present(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1", "c2"])
        _write_prompt_payload(store, rid, "c1", downstream_usable=True)
        _write_prompt_payload(store, rid, "c2", downstream_usable=False)
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        records = result["metadata"]["stage_summary"]["execution_records"]
        skipped = [
            r for r in records
            if r["execution_status"] == STATUS_SKIPPED_NOT_RUNNABLE
        ]
        assert len(skipped) == 1
        assert skipped[0]["candidate_id"] == "c2"


# =====================================================================
#  TestEventEmission
# =====================================================================

class TestEventEmission:
    def test_started_and_completed_events(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        events = []
        final_recommendation_handler(
            run, store, "final_model_decision",
            event_callback=lambda e: events.append(e),
        )
        types = [e["event_type"] for e in events]
        assert "final_model_started" in types
        assert "final_model_completed" in types

    def test_failed_event_on_all_fail(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        events = []
        final_recommendation_handler(
            run, store, "final_model_decision",
            model_executor=_failing_executor,
            event_callback=lambda e: events.append(e),
        )
        types = [e["event_type"] for e in events]
        assert "final_model_started" in types
        assert "final_model_failed" in types

    def test_no_events_without_callback(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        # Should not crash when no callback
        result = final_recommendation_handler(
            run, store, "final_model_decision",
        )
        assert result["outcome"] == "completed"

    def test_event_metadata_includes_counts(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1", "c2"])
        events = []
        final_recommendation_handler(
            run, store, "final_model_decision",
            event_callback=lambda e: events.append(e),
        )
        completed_events = [
            e for e in events
            if e["event_type"] == "final_model_completed"
        ]
        assert len(completed_events) == 1
        meta = completed_events[0]["metadata"]
        assert "total_completed" in meta


# =====================================================================
#  TestPolicyGuardrailsExplicit
# =====================================================================

class TestPolicyGuardrailsExplicit:
    def test_blockers_preserved_in_output(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1"])
        _write_prompt_payload(
            store, rid, "c1",
            overall_outcome="blocked",
            blocking_reasons=["capital exceeded", "position limit"],
            downstream_usable=True,
        )
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        echo = art["data"]["policy_guardrail_echo"]
        assert echo["overall_outcome"] == "blocked"
        assert len(echo["blockers"]) == 2
        assert "capital exceeded" in echo["blockers"]

    def test_cautions_preserved(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1"])
        _write_prompt_payload(
            store, rid, "c1",
            overall_outcome="caution",
            caution_reasons=["near earnings"],
        )
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        echo = art["data"]["policy_guardrail_echo"]
        assert echo["cautions"] == ["near earnings"]

    def test_restrictions_preserved(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _write_prompt_payload_summary(store, rid, ["c1"])
        _write_prompt_payload(
            store, rid, "c1",
            overall_outcome="restricted",
            restriction_reasons=["sector cap reached"],
        )
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        echo = art["data"]["policy_guardrail_echo"]
        assert echo["restrictions"] == ["sector cap reached"]


# =====================================================================
#  TestQualityPropagation
# =====================================================================

class TestQualityPropagation:
    def test_quality_in_output(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        quality = art["data"]["quality"]
        assert "response_quality" in quality
        assert "degraded_reasons" in quality
        assert "downstream_usable" in quality


# =====================================================================
#  TestOrchestratorIntegration
# =====================================================================

class TestOrchestratorIntegration:
    def test_wired_as_default_handler(self):
        from app.services.pipeline_final_recommendation_stage import (
            final_recommendation_handler as real_handler,
        )
        handlers = get_default_handlers()
        assert handlers["final_model_decision"] is real_handler

    def test_execute_stage_integration(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])

        from app.services.pipeline_final_recommendation_stage import (
            final_recommendation_handler,
        )
        result = execute_stage(
            run, store, "final_model_decision",
            handler=final_recommendation_handler,
            dependency_map=get_default_dependency_map(),
        )
        assert result["outcome"] == "completed"

    def test_pipeline_runs_with_real_handler(self):
        """Pipeline with all stubs except final_model_decision."""
        from app.services.pipeline_final_recommendation_stage import (
            final_recommendation_handler,
        )

        def _pp_handler(run, artifact_store, stage_key, **kwargs):
            """Stub prompt_payload that writes artifacts."""
            rid = run["run_id"]
            _populate_prompt_payloads(artifact_store, rid, ["c1"])
            return {
                "outcome": "completed",
                "summary_counts": {"total_built": 1},
                "artifacts": [],
                "metadata": {},
                "error": None,
            }

        result = _all_stub_pipeline(
            handlers={
                "prompt_payload": _pp_handler,
                "final_model_decision": final_recommendation_handler,
            },
        )
        assert result["run"]["status"] == "completed"

    def test_dependency_on_prompt_payload(self):
        deps = get_default_dependency_map()
        assert "prompt_payload" in deps["final_model_decision"]


# =====================================================================
#  TestForwardCompatibility
# =====================================================================

class TestForwardCompatibility:
    """Verify Step 14 outputs are ready for Step 15 consumption."""

    def test_recommendation_artifact_retrievable_by_key(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        assert art is not None

    def test_summary_artifact_retrievable(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_model_summary",
        )
        assert art is not None
        data = art["data"]
        assert "output_artifact_refs" in data
        assert "execution_records" in data

    def test_output_has_fields_for_ui_rendering(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        # Fields useful for downstream card rendering / ledger
        assert "recommendation" in data
        assert "policy_guardrail_echo" in data
        assert "quality" in data
        assert "model_metadata" in data
        assert "final_status" in data
        assert "candidate_id" in data
        assert "symbol" in data

    def test_output_has_source_lineage(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        assert "source_prompt_payload_ref" in data

    def test_output_has_downstream_usable(self):
        run, store = _make_run_and_store()
        rid = run["run_id"]
        _populate_prompt_payloads(store, rid, ["c1"])
        final_recommendation_handler(run, store, "final_model_decision")

        art = get_artifact_by_key(
            store, "final_model_decision", "final_c1",
        )
        data = art["data"]
        assert data["quality"]["downstream_usable"] is True
