"""Tests for pipeline_final_response_stage — Step 15.

Covers:
  - Status vocabulary / constant registration
  - Per-candidate response normalization (ready, degraded, skipped, failed)
  - Response contract shape stability
  - Policy summary preservation and consistency warning propagation
  - UI hint shaping (display_title, bucket, priority)
  - Ledger row structure stability
  - Missing final model summary
  - Empty candidate set
  - Successful per-candidate normalization
  - Skipped candidate handling
  - Degraded response handling
  - Failed response handling
  - Consistency warning propagation
  - Candidate decision ledger building
  - Ledger counts (action, policy, consistency)
  - Artifact creation and lineage
  - Stage summary contents
  - Event emission (started/completed/failed)
  - Orchestrator integration (wiring, deps, pipeline run)
  - Forward compatibility for UI/reporting consumers
"""

import pytest

from app.services.pipeline_final_response_stage import (
    _STAGE_KEY,
    _FINAL_RESPONSE_VERSION,
    _UPSTREAM_STAGE_KEY,
    STATUS_READY,
    STATUS_READY_DEGRADED,
    STATUS_SKIPPED,
    STATUS_FAILED,
    VALID_RESPONSE_STATUSES,
    normalize_final_response,
    build_ledger_row,
    final_response_handler,
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

def _make_run_and_store(run_id="test-fr-001"):
    """Create a fresh run+store with upstream stages completed.

    Completes through final_model_decision (since that stage
    runs before final_response_normalization in the canonical order).
    """
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    for stage in (
        "market_data", "market_model_analysis",
        "scanners", "candidate_selection",
        "shared_context", "candidate_enrichment",
        "events", "policy", "orchestration",
        "prompt_payload", "final_model_decision",
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


# ── Step 14 output builders ────────────────────────────────────

def _make_final_model_output(
    candidate_id="c1",
    symbol="SPY",
    *,
    final_status="completed",
    decision="buy",
    conviction=0.7,
    overall_outcome="eligible",
    blocking_reasons=None,
    caution_reasons=None,
    restriction_reasons=None,
    degraded_reasons=None,
    downstream_usable=True,
    provider="stub",
    model_name="default_model_executor",
    latency_ms=0,
    warnings=None,
    run_id="test-fr-001",
):
    """Build a Step 14 normalized recommendation output."""
    return {
        "final_recommendation_version": "1.0",
        "run_id": run_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "source_prompt_payload_ref": f"art-pp-{candidate_id}",
        "final_status": final_status,
        "model_execution_status": (
            "success" if final_status != "failed" else "error"
        ),
        "recommendation": {
            "decision": decision,
            "conviction": conviction,
            "rationale_summary": f"Stub recommendation for {symbol}",
            "key_supporting_points": [
                f"Policy outcome: {overall_outcome}",
            ],
            "key_risks": ["market_downturn"],
            "market_alignment": "neutral",
            "portfolio_fit": "acceptable",
            "event_sensitivity": "low",
            "sizing_guidance": "standard",
        },
        "policy_guardrail_echo": {
            "overall_outcome": overall_outcome,
            "blockers": blocking_reasons or [],
            "cautions": caution_reasons or [],
            "restrictions": restriction_reasons or [],
        },
        "quality": {
            "response_quality": (
                "full" if final_status == "completed" else "degraded"
            ),
            "degraded_reasons": degraded_reasons or [],
            "downstream_usable": downstream_usable,
        },
        "model_metadata": {
            "provider": provider,
            "model_name": model_name,
            "latency_ms": latency_ms,
            "override_used": False,
            "routing_metadata": None,
            "input_mode": "structured",
        },
        "raw_response_excerpt": {
            "keys_present": ["decision", "conviction"],
            "decision": decision,
            "conviction": conviction,
        },
        "warnings": warnings or [],
        "notes": [],
        "metadata": {
            "normalization_timestamp": "2026-03-11T12:00:00+00:00",
            "recommendation_version": "1.0",
            "stage_key": "final_model_decision",
            "policy_outcome": overall_outcome,
            "downstream_usable": downstream_usable,
        },
    }


def _write_final_model_output(
    store, run_id, candidate_id, output=None, **overrides,
):
    """Write a Step 14 per-candidate final model output artifact."""
    if output is None:
        output = _make_final_model_output(
            candidate_id=candidate_id,
            run_id=run_id,
            **overrides,
        )
    art = build_artifact_record(
        run_id=run_id,
        stage_key="final_model_decision",
        artifact_key=f"final_{candidate_id}",
        artifact_type="final_model_output",
        data=output,
        candidate_id=candidate_id,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _write_final_model_summary(
    store, run_id, candidate_ids, **overrides,
):
    """Write a Step 14 final_model_summary artifact."""
    records = [
        {
            "candidate_id": cid,
            "execution_status": "completed",
        }
        for cid in candidate_ids
    ]
    summary = {
        "stage_key": "final_model_decision",
        "stage_status": overrides.get("stage_status", "success"),
        "total_candidates_loaded": len(candidate_ids),
        "total_runnable": len(candidate_ids),
        "total_completed": len(candidate_ids),
        "total_degraded": 0,
        "total_skipped": 0,
        "total_failed": 0,
        "candidate_ids_processed": candidate_ids,
        "output_artifact_refs": {
            cid: f"art-fm-{cid}" for cid in candidate_ids
        },
        "execution_records": records,
        "warnings": [],
        "elapsed_ms": 1,
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="final_model_decision",
        artifact_key="final_model_summary",
        artifact_type="final_model_summary",
        data=summary,
    )
    put_artifact(store, art, overwrite=True)
    return art["artifact_id"]


def _populate_final_model_artifacts(
    store, run_id, candidate_ids, **per_candidate_overrides,
):
    """Write final model summary + per-candidate outputs."""
    _write_final_model_summary(store, run_id, candidate_ids)
    for cid in candidate_ids:
        overrides = per_candidate_overrides.get(cid, {})
        _write_final_model_output(store, run_id, cid, **overrides)


# =====================================================================
#  Constants and vocabulary tests
# =====================================================================

class TestConstants:
    def test_stage_key(self):
        assert _STAGE_KEY == "final_response_normalization"

    def test_upstream_stage_key(self):
        assert _UPSTREAM_STAGE_KEY == "final_model_decision"

    def test_version(self):
        assert _FINAL_RESPONSE_VERSION == "1.0"

    def test_status_values(self):
        assert STATUS_READY == "ready"
        assert STATUS_READY_DEGRADED == "ready_degraded"
        assert STATUS_SKIPPED == "skipped"
        assert STATUS_FAILED == "failed"

    def test_valid_statuses_frozenset(self):
        assert isinstance(VALID_RESPONSE_STATUSES, frozenset)
        assert VALID_RESPONSE_STATUSES == {
            "ready", "ready_degraded", "skipped", "failed",
        }

    def test_stage_in_pipeline(self):
        assert "final_response_normalization" in PIPELINE_STAGES

    def test_stage_is_last(self):
        assert PIPELINE_STAGES[-1] == "final_response_normalization"

    def test_event_types_registered(self):
        for et in (
            "final_response_started",
            "final_response_completed",
            "final_response_failed",
        ):
            assert et in VALID_EVENT_TYPES, f"{et} not in VALID_EVENT_TYPES"

    def test_artifact_types_registered(self):
        for at in (
            "final_decision_response",
            "final_response_ledger",
            "final_response_summary",
        ):
            assert at in VALID_ARTIFACT_TYPES, (
                f"{at} not in VALID_ARTIFACT_TYPES"
            )


# =====================================================================
#  normalize_final_response tests
# =====================================================================

class TestNormalizeFinalResponse:
    def test_basic_normalization(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        assert resp["final_response_version"] == "1.0"
        assert resp["run_id"] == "run-1"
        assert resp["candidate_id"] == "c1"
        assert resp["response_status"] == STATUS_READY

    def test_response_contract_shape(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        required_keys = {
            "final_response_version", "run_id", "candidate_id",
            "source_final_model_ref", "response_status",
            "candidate_identity", "recommendation_summary",
            "policy_summary", "execution_summary",
            "quality_summary", "source_refs", "ui_hints",
            "ledger_metadata",
        }
        assert required_keys.issubset(resp.keys())

    def test_candidate_identity_fields(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        identity = resp["candidate_identity"]
        assert identity["symbol"] == "SPY"
        assert "scanner_key" in identity
        assert "strategy_type" in identity
        assert "direction" in identity
        assert "rank_position" in identity
        assert "rank_score" in identity

    def test_recommendation_summary_maps_decision_to_action(self):
        output = _make_final_model_output(decision="buy")
        resp = normalize_final_response(output, "run-1")
        assert resp["recommendation_summary"]["action"] == "buy"
        assert resp["recommendation_summary"]["conviction"] == 0.7

    def test_recommendation_summary_fields(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        rec = resp["recommendation_summary"]
        assert "rationale_summary" in rec
        assert isinstance(rec["key_supporting_points"], list)
        assert isinstance(rec["key_risks"], list)
        assert "event_sensitivity" in rec
        assert "portfolio_fit" in rec
        assert "sizing_guidance" in rec

    def test_policy_summary_preserved(self):
        output = _make_final_model_output(
            overall_outcome="caution",
            caution_reasons=["earnings_imminent"],
        )
        resp = normalize_final_response(output, "run-1")
        assert resp["policy_summary"]["overall_outcome"] == "caution"
        assert "earnings_imminent" in resp["policy_summary"]["cautions"]

    def test_consistency_warning_propagated(self):
        output = _make_final_model_output(
            overall_outcome="blocked",
            decision="buy",
            warnings=["model_recommends_buy_despite_blocked_policy"],
        )
        resp = normalize_final_response(output, "run-1")
        assert resp["policy_summary"]["consistency_warning"] == (
            "model_recommends_buy_despite_blocked_policy"
        )

    def test_no_consistency_warning_when_clean(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        assert resp["policy_summary"]["consistency_warning"] is None

    def test_execution_summary(self):
        output = _make_final_model_output(
            provider="openai", model_name="gpt-4",
        )
        resp = normalize_final_response(output, "run-1")
        assert resp["execution_summary"]["provider"] == "openai"
        assert resp["execution_summary"]["model_name"] == "gpt-4"

    def test_quality_summary(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        assert resp["quality_summary"]["downstream_usable"] is True
        assert resp["quality_summary"]["response_quality"] == "full"

    def test_source_refs(self):
        output = _make_final_model_output()
        resp = normalize_final_response(
            output, "run-1", source_artifact_ref="art-fm-c1",
        )
        assert resp["source_refs"]["final_model_artifact_ref"] == "art-fm-c1"
        assert resp["source_refs"]["prompt_payload_ref"] == "art-pp-c1"

    def test_degraded_status_mapping(self):
        output = _make_final_model_output(
            final_status="completed_degraded",
            degraded_reasons=["partial_parse"],
        )
        resp = normalize_final_response(output, "run-1")
        assert resp["response_status"] == STATUS_READY_DEGRADED

    def test_skipped_status_mapping(self):
        output = _make_final_model_output(
            final_status="skipped_not_runnable",
            downstream_usable=False,
        )
        resp = normalize_final_response(output, "run-1")
        assert resp["response_status"] == STATUS_SKIPPED

    def test_failed_status_mapping(self):
        output = _make_final_model_output(
            final_status="failed",
            downstream_usable=False,
        )
        resp = normalize_final_response(output, "run-1")
        assert resp["response_status"] == STATUS_FAILED


# =====================================================================
#  UI hints tests
# =====================================================================

class TestUIHints:
    def test_display_title_format(self):
        output = _make_final_model_output(symbol="QQQ", decision="hold")
        resp = normalize_final_response(output, "run-1")
        assert resp["ui_hints"]["display_title"] == "QQQ — HOLD"

    def test_display_symbol(self):
        output = _make_final_model_output(symbol="IWM")
        resp = normalize_final_response(output, "run-1")
        assert resp["ui_hints"]["display_symbol"] == "IWM"

    def test_recommendation_bucket_buy(self):
        output = _make_final_model_output(decision="buy")
        resp = normalize_final_response(output, "run-1")
        assert resp["ui_hints"]["recommendation_bucket"] == "buy"

    def test_recommendation_bucket_pass(self):
        output = _make_final_model_output(decision="pass")
        resp = normalize_final_response(output, "run-1")
        assert resp["ui_hints"]["recommendation_bucket"] == "pass"

    def test_recommendation_bucket_unknown(self):
        output = _make_final_model_output(decision=None)
        resp = normalize_final_response(output, "run-1")
        assert resp["ui_hints"]["recommendation_bucket"] == "unknown"

    def test_review_priority_buy_is_highest(self):
        output = _make_final_model_output(decision="buy")
        resp = normalize_final_response(output, "run-1")
        assert resp["ui_hints"]["review_priority"] == 1

    def test_review_priority_pass_is_lower(self):
        output = _make_final_model_output(decision="pass")
        resp = normalize_final_response(output, "run-1")
        assert resp["ui_hints"]["review_priority"] == 4

    def test_missing_symbol_fallback(self):
        output = _make_final_model_output()
        output["symbol"] = None
        resp = normalize_final_response(output, "run-1")
        assert resp["ui_hints"]["display_symbol"] == "???"
        assert "???" in resp["ui_hints"]["display_title"]


# =====================================================================
#  Ledger row tests
# =====================================================================

class TestLedgerRow:
    def test_basic_row_structure(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        row = build_ledger_row(resp)
        required_keys = {
            "candidate_id", "symbol", "action", "conviction",
            "policy_outcome", "response_status", "consistency_flag",
            "provider", "model_name", "downstream_usable",
            "source_response_ref", "rank_position", "scanner_key",
        }
        assert required_keys == set(row.keys())

    def test_row_values(self):
        output = _make_final_model_output(
            candidate_id="c2", symbol="QQQ", decision="hold",
            conviction=0.5, overall_outcome="caution",
            provider="openai",
        )
        resp = normalize_final_response(output, "run-1")
        row = build_ledger_row(resp)
        assert row["candidate_id"] == "c2"
        assert row["symbol"] == "QQQ"
        assert row["action"] == "hold"
        assert row["conviction"] == 0.5
        assert row["policy_outcome"] == "caution"
        assert row["response_status"] == STATUS_READY
        assert row["provider"] == "openai"
        assert row["downstream_usable"] is True

    def test_consistency_flag_present(self):
        output = _make_final_model_output(
            overall_outcome="blocked", decision="buy",
            warnings=["model_recommends_buy_despite_blocked_policy"],
        )
        resp = normalize_final_response(output, "run-1")
        row = build_ledger_row(resp)
        assert row["consistency_flag"] == (
            "model_recommends_buy_despite_blocked_policy"
        )

    def test_consistency_flag_none_when_clean(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        row = build_ledger_row(resp)
        assert row["consistency_flag"] is None


# =====================================================================
#  Handler contract tests
# =====================================================================

class TestHandlerContract:
    def test_return_shape(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(store, "test-fr-001", ["c1"])
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert "outcome" in result
        assert "summary_counts" in result
        assert "artifacts" in result
        assert "metadata" in result
        assert "error" in result

    def test_summary_counts_keys(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(store, "test-fr-001", ["c1"])
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        sc = result["summary_counts"]
        assert "total_ready" in sc
        assert "total_degraded" in sc
        assert "total_skipped" in sc
        assert "total_failed" in sc


# =====================================================================
#  Missing final model summary
# =====================================================================

class TestMissingFinalModelSummary:
    def test_fails_with_no_final_model_source(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_FINAL_MODEL_SOURCE"

    def test_emits_failed_event(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "final_response_normalization")
        events = []
        result = final_response_handler(
            run, store, "final_response_normalization",
            event_callback=events.append,
        )
        assert result["outcome"] == "failed"
        types = [e["event_type"] for e in events]
        assert "final_response_started" in types
        assert "final_response_failed" in types


# =====================================================================
#  Vacuous completion (empty candidate set)
# =====================================================================

class TestVacuousCompletion:
    def test_zero_candidates(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", [])
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_ready"] == 0
        assert result["error"] is None

    def test_vacuous_writes_ledger_and_summary(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", [])
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        ledger_art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        assert ledger_art is not None

        summary_art = get_artifact_by_key(
            store, "final_response_normalization",
            "final_response_summary",
        )
        assert summary_art is not None


# =====================================================================
#  Single candidate — successful normalization
# =====================================================================

class TestSingleCandidate:
    def test_outcome_completed(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_ready"] == 1

    def test_response_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        assert art is not None
        assert art["artifact_type"] == "final_decision_response"
        data = art["data"]
        assert data["candidate_id"] == "c1"
        assert data["response_status"] == STATUS_READY

    def test_ledger_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        assert art is not None
        assert art["artifact_type"] == "final_response_ledger"
        assert len(art["data"]["ledger_rows"]) == 1

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "final_response_summary",
        )
        assert art is not None
        assert art["artifact_type"] == "final_response_summary"


# =====================================================================
#  Multiple candidates
# =====================================================================

class TestMultipleCandidates:
    def test_two_candidates_both_ready(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1", "c2"],
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_ready"] == 2
        assert len(result["artifacts"]) == 2

    def test_artifacts_keyed_by_candidate(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1", "c2"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        for cid in ("c1", "c2"):
            art = get_artifact_by_key(
                store, "final_response_normalization",
                f"response_{cid}",
            )
            assert art is not None
            assert art["data"]["candidate_id"] == cid


# =====================================================================
#  Skipped candidate handling
# =====================================================================

class TestSkippedCandidate:
    def test_skipped_mapped_correctly(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            final_status="skipped_not_runnable",
            downstream_usable=False,
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_skipped"] == 1
        assert result["summary_counts"]["total_ready"] == 0

    def test_skipped_appears_in_ledger(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            final_status="skipped_not_runnable",
            downstream_usable=False,
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        ledger = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        assert "c1" in ledger["data"]["candidate_ids_skipped"]

    def test_skipped_is_not_stage_failure(self):
        """Skipped candidates should not cause the stage to fail."""
        run, store = _make_run_and_store()
        _write_final_model_summary(
            store, "test-fr-001", ["c1", "c2"],
        )
        _write_final_model_output(
            store, "test-fr-001", "c1",
            final_status="skipped_not_runnable",
            downstream_usable=False,
        )
        _write_final_model_output(
            store, "test-fr-001", "c2",
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_skipped"] == 1
        assert result["summary_counts"]["total_ready"] == 1


# =====================================================================
#  Degraded response handling
# =====================================================================

class TestDegradedResponse:
    def test_degraded_status(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            final_status="completed_degraded",
            degraded_reasons=["partial_parse"],
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_degraded"] == 1

    def test_degraded_stage_status(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            final_status="completed_degraded",
            degraded_reasons=["partial_parse"],
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        summary = result["metadata"]["stage_summary"]
        assert summary["stage_status"] == "degraded"


# =====================================================================
#  Failed response handling
# =====================================================================

class TestFailedResponse:
    def test_all_candidates_failed(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            final_status="failed",
            downstream_usable=False,
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "FINAL_RESPONSE_ALL_FAILED"

    def test_missing_upstream_artifact(self):
        """If Step 14 summary lists candidate but no artifact exists."""
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        # Do NOT write the per-candidate artifact
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["total_failed"] == 1

    def test_partial_failure(self):
        """Some succeed, some fail → completed (degraded)."""
        run, store = _make_run_and_store()
        _write_final_model_summary(
            store, "test-fr-001", ["c1", "c2"],
        )
        _write_final_model_output(store, "test-fr-001", "c1")
        _write_final_model_output(
            store, "test-fr-001", "c2",
            final_status="failed",
            downstream_usable=False,
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        # At least one ready → outcome "completed" but degraded stage status
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_ready"] == 1
        assert result["summary_counts"]["total_failed"] == 1


# =====================================================================
#  Consistency warning propagation
# =====================================================================

class TestConsistencyWarningPropagation:
    def test_warning_reaches_policy_summary(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            overall_outcome="blocked",
            decision="buy",
            warnings=["model_recommends_buy_despite_blocked_policy"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        assert art["data"]["policy_summary"]["consistency_warning"] == (
            "model_recommends_buy_despite_blocked_policy"
        )

    def test_warning_in_ledger_row(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            overall_outcome="blocked",
            decision="buy",
            warnings=["model_recommends_buy_despite_blocked_policy"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        ledger = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        rows = ledger["data"]["ledger_rows"]
        assert rows[0]["consistency_flag"] == (
            "model_recommends_buy_despite_blocked_policy"
        )

    def test_warning_counted_in_ledger(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            overall_outcome="blocked",
            decision="buy",
            warnings=["model_recommends_buy_despite_blocked_policy"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        ledger = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        cw = ledger["data"]["counts_by_consistency_warning"]
        assert "model_recommends_buy_despite_blocked_policy" in cw


# =====================================================================
#  Candidate decision ledger tests
# =====================================================================

class TestCandidateDecisionLedger:
    def test_ledger_shape(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1", "c2"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        ledger = art["data"]
        required_keys = {
            "ledger_version", "run_id", "stage_key",
            "candidate_ids_processed", "candidate_ids_ready",
            "candidate_ids_degraded", "candidate_ids_failed",
            "candidate_ids_skipped", "ledger_rows",
            "counts_by_action", "counts_by_policy_outcome",
            "counts_by_consistency_warning",
            "provider_usage", "model_usage",
            "stage_status_rollup", "warnings", "generated_at",
        }
        assert required_keys.issubset(ledger.keys())

    def test_ledger_counts_by_action(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(
            store, "test-fr-001", ["c1", "c2"],
        )
        _write_final_model_output(
            store, "test-fr-001", "c1", decision="buy",
        )
        _write_final_model_output(
            store, "test-fr-001", "c2",
            decision="hold", symbol="QQQ",
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        counts = art["data"]["counts_by_action"]
        assert counts["buy"] == 1
        assert counts["hold"] == 1

    def test_ledger_counts_by_policy(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(
            store, "test-fr-001", ["c1", "c2"],
        )
        _write_final_model_output(
            store, "test-fr-001", "c1",
            overall_outcome="eligible",
        )
        _write_final_model_output(
            store, "test-fr-001", "c2",
            overall_outcome="caution", symbol="QQQ",
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        counts = art["data"]["counts_by_policy_outcome"]
        assert counts["eligible"] == 1
        assert counts["caution"] == 1

    def test_provider_usage_in_ledger(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            provider="openai",
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        assert art["data"]["provider_usage"]["openai"] == 1

    def test_ledger_stage_status_rollup_success(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        assert art["data"]["stage_status_rollup"] == "success"


# =====================================================================
#  Stage summary tests
# =====================================================================

class TestStageSummary:
    def test_summary_shape(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        summary = result["metadata"]["stage_summary"]
        required_keys = {
            "stage_key", "stage_status",
            "total_candidates_loaded", "total_ready",
            "total_degraded", "total_skipped", "total_failed",
            "output_artifact_refs", "ledger_artifact_ref",
            "counts_by_action", "counts_by_policy_outcome",
            "provider_usage_counts", "model_usage_counts",
            "warnings", "degraded_reasons",
            "elapsed_ms", "generated_at",
        }
        assert required_keys.issubset(summary.keys())

    def test_summary_counts_match(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1", "c2"],
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        summary = result["metadata"]["stage_summary"]
        assert summary["total_candidates_loaded"] == 2
        assert summary["total_ready"] == 2

    def test_summary_has_ledger_ref(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        summary = result["metadata"]["stage_summary"]
        assert summary["ledger_artifact_ref"] is not None

    def test_summary_output_artifact_refs(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1", "c2"],
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        summary = result["metadata"]["stage_summary"]
        assert "c1" in summary["output_artifact_refs"]
        assert "c2" in summary["output_artifact_refs"]


# =====================================================================
#  Event emission tests
# =====================================================================

class TestEventEmission:
    def test_started_and_completed_events(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        events = []
        final_response_handler(
            run, store, "final_response_normalization",
            event_callback=events.append,
        )
        types = [e["event_type"] for e in events]
        assert "final_response_started" in types
        assert "final_response_completed" in types

    def test_failed_event_on_no_source(self):
        run, store = _make_run_and_store()
        mark_stage_running(run, "final_response_normalization")
        events = []
        final_response_handler(
            run, store, "final_response_normalization",
            event_callback=events.append,
        )
        types = [e["event_type"] for e in events]
        assert "final_response_failed" in types

    def test_completed_event_metadata(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1", "c2"],
        )
        mark_stage_running(run, "final_response_normalization")
        events = []
        final_response_handler(
            run, store, "final_response_normalization",
            event_callback=events.append,
        )
        completed = [
            e for e in events
            if e["event_type"] == "final_response_completed"
        ]
        assert len(completed) == 1
        meta = completed[0]["metadata"]
        assert meta["total_ready"] == 2

    def test_no_events_without_callback(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        # Should not raise
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "completed"


# =====================================================================
#  Artifact lineage tests
# =====================================================================

class TestArtifactLineage:
    def test_response_links_to_step14_artifact(self):
        run, store = _make_run_and_store()
        art_id = _write_final_model_output(
            store, "test-fr-001", "c1",
        )
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        resp_art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        data = resp_art["data"]
        assert data["source_final_model_ref"] == art_id
        assert data["source_refs"]["final_model_artifact_ref"] == art_id

    def test_response_links_to_prompt_payload(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        resp_art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        data = resp_art["data"]
        # prompt_payload_ref comes from Step 14's source_prompt_payload_ref
        assert data["source_refs"]["prompt_payload_ref"] == "art-pp-c1"


# =====================================================================
#  Policy summary preservation
# =====================================================================

class TestPolicySummaryPreservation:
    def test_blockers_preserved(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            overall_outcome="blocked",
            blocking_reasons=["max_position_reached"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        ps = art["data"]["policy_summary"]
        assert ps["overall_outcome"] == "blocked"
        assert "max_position_reached" in ps["blockers"]

    def test_cautions_preserved(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            overall_outcome="caution",
            caution_reasons=["earnings_imminent"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        ps = art["data"]["policy_summary"]
        assert "earnings_imminent" in ps["cautions"]


# =====================================================================
#  Quality propagation
# =====================================================================

class TestQualityPropagation:
    def test_full_quality(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        qs = art["data"]["quality_summary"]
        assert qs["downstream_usable"] is True
        assert qs["response_quality"] == "full"

    def test_degraded_quality_reasons(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            final_status="completed_degraded",
            degraded_reasons=["missing_event_data"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        qs = art["data"]["quality_summary"]
        assert "missing_event_data" in qs["degraded_reasons"]


# =====================================================================
#  Orchestrator integration
# =====================================================================

class TestOrchestratorIntegration:
    def test_default_handler_wired(self):
        handlers = get_default_handlers()
        assert "final_response_normalization" in handlers
        assert handlers["final_response_normalization"] is final_response_handler

    def test_dependency_map(self):
        deps = get_default_dependency_map()
        assert deps["final_response_normalization"] == [
            "final_model_decision",
        ]

    def test_no_stubs_remain(self):
        """All 12 canonical stages now have real handlers."""
        handlers = get_default_handlers()
        for stage in PIPELINE_STAGES:
            h = handlers[stage]
            # Stub handlers return {"stub": True} in metadata —
            # real handlers do not
            assert h.__name__ != "_stub_handler", (
                f"Stage '{stage}' still uses stub handler"
            )

    def test_pipeline_run_all_stubbed(self):
        """Full pipeline with all stages stubbed completes."""
        result = _all_stub_pipeline(run_id="test-fr-orch-001")
        sr = {s["stage_key"]: s for s in result["stage_results"]}
        assert sr["final_response_normalization"]["outcome"] == "completed"

    def test_pipeline_all_stages_present(self):
        result = _all_stub_pipeline(run_id="test-fr-orch-002")
        sr = {s["stage_key"] for s in result["stage_results"]}
        for stage in PIPELINE_STAGES:
            assert stage in sr, f"Stage '{stage}' missing from results"


# =====================================================================
#  Forward compatibility / replay tests
# =====================================================================

class TestForwardCompatibility:
    def test_response_version_field(self):
        output = _make_final_model_output()
        resp = normalize_final_response(output, "run-1")
        assert resp["final_response_version"] == "1.0"

    def test_ledger_version_field(self):
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        assert art["data"]["ledger_version"] == "1.0"

    def test_unknown_fields_in_source_pass_through(self):
        """Future Step 14 fields don't break normalization."""
        output = _make_final_model_output()
        output["future_field_xyz"] = "new_data"
        resp = normalize_final_response(output, "run-1")
        # Should not crash, status should still be ready
        assert resp["response_status"] == STATUS_READY

    def test_response_artifact_retrievable_by_candidate_id(self):
        """Downstream consumers can iterate by candidate_id."""
        run, store = _make_run_and_store()
        _populate_final_model_artifacts(
            store, "test-fr-001", ["c1", "c2", "c3"],
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        for cid in ("c1", "c2", "c3"):
            art = get_artifact_by_key(
                store, "final_response_normalization",
                f"response_{cid}",
            )
            assert art is not None
            assert art["data"]["candidate_id"] == cid

    def test_ledger_rows_sortable_by_conviction(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(
            store, "test-fr-001", ["c1", "c2"],
        )
        _write_final_model_output(
            store, "test-fr-001", "c1",
            conviction=0.3,
        )
        _write_final_model_output(
            store, "test-fr-001", "c2",
            conviction=0.9, symbol="QQQ",
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization",
            "candidate_decision_ledger",
        )
        rows = art["data"]["ledger_rows"]
        sorted_rows = sorted(
            rows,
            key=lambda r: r.get("conviction") or 0,
            reverse=True,
        )
        assert sorted_rows[0]["conviction"] == 0.9
        assert sorted_rows[1]["conviction"] == 0.3


# =====================================================================
#  Blocked candidate still produces output
# =====================================================================

class TestBlockedCandidateProducesOutput:
    def test_blocked_with_output_is_ready(self):
        """Step 14 may produce output even for blocked candidates."""
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            overall_outcome="blocked",
            blocking_reasons=["max_position"],
            decision="pass",
            downstream_usable=True,
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        assert result["outcome"] == "completed"
        art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        assert art["data"]["response_status"] == STATUS_READY
        assert art["data"]["policy_summary"]["overall_outcome"] == "blocked"


# =====================================================================
#  Execution summary propagation
# =====================================================================

class TestExecutionSummaryPropagation:
    def test_provider_model_in_response(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            provider="anthropic", model_name="claude-3",
        )
        mark_stage_running(run, "final_response_normalization")
        final_response_handler(
            run, store, "final_response_normalization",
        )
        art = get_artifact_by_key(
            store, "final_response_normalization", "response_c1",
        )
        es = art["data"]["execution_summary"]
        assert es["provider"] == "anthropic"
        assert es["model_name"] == "claude-3"

    def test_provider_in_summary(self):
        run, store = _make_run_and_store()
        _write_final_model_summary(store, "test-fr-001", ["c1"])
        _write_final_model_output(
            store, "test-fr-001", "c1",
            provider="anthropic",
        )
        mark_stage_running(run, "final_response_normalization")
        result = final_response_handler(
            run, store, "final_response_normalization",
        )
        summary = result["metadata"]["stage_summary"]
        assert summary["provider_usage_counts"]["anthropic"] == 1
