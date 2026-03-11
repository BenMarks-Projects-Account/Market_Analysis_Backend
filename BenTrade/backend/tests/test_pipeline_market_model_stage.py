"""Tests for Pipeline Market Model-Analysis Stage v1.0.

Coverage targets:
─── Step 4 artifact retrieval
    - successful retrieval of stage summary
    - successful retrieval of per-engine artifacts
    - missing stage summary artifact
    - missing per-engine artifact
─── Eligibility filtering
    - eligible_for_model_analysis flag respected
    - disabled engines skipped
    - missing artifact → skipped_missing_artifact
    - empty/null payload → skipped_invalid_payload
    - non-eligible in summary → skipped_not_eligible
─── Engine output normalization
    - normalize_engine_for_model produces stable shape
    - engine_key preserved
    - source_artifact_ref preserved
    - compact_summary extracted
    - warnings propagated
─── Model execution seam
    - model_executor called per eligible engine
    - mock executor injection
    - executor exceptions captured
─── Bounded parallel execution
    - all eligible engines analyzed successfully
    - configurable max_workers
    - deterministic result keyed by engine_key
─── Per-engine model-analysis record
    - build_model_analysis_record shape
    - all status values
    - timing fields
    - error attachment
    - artifact refs
    - downstream_usable flag
─── Artifact creation and lineage
    - per-engine model output artifact written
    - no artifact for failed analysis
    - stage summary artifact written
    - artifact lineage (run_id, stage_key, engine_key)
─── Stage summary artifact
    - engines_analyzed list
    - engines_failed list
    - engines_skipped with reasons
    - artifact_refs mapping
    - stage_status rollup
    - degraded_reasons
    - engine_summaries
─── Event emission
    - model_analysis_started emitted
    - model_analysis_completed emitted
    - model_analysis_failed emitted
    - events have engine_key in metadata
─── Partial failure semantics
    - all succeed → completed / success
    - mixed success/failure → completed / degraded
    - all fail → failed / ALL_ANALYSES_FAILED
    - zero eligible → completed / no_eligible_inputs
─── Handler contract (orchestrator-compatible)
    - returns outcome/summary_counts/artifacts/metadata/error
    - summary_counts has analysis counts
    - metadata has analysis_records
─── Orchestrator integration
    - market_model_analysis handler wired as default
    - stage result flows through orchestrator
─── Override mode
    - model_results_override bypasses executor
─── Step 4 artifact compatibility
    - uses get_artifact_by_key with correct stage/key
─── Output stability
    - record shape has all expected fields
    - summary shape has all expected fields
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.services.pipeline_market_model_stage import (
    ANALYSIS_STATUSES,
    DEFAULT_MODEL_MAX_WORKERS,
    _STAGE_KEY,
    _determine_eligibility,
    _execute_analyses_parallel,
    _get_engine_artifact,
    _get_market_stage_summary,
    build_model_analysis_record,
    build_model_stage_summary,
    market_model_stage_handler,
    normalize_engine_for_model,
)
from app.services.pipeline_artifact_store import (
    build_artifact_record,
    create_artifact_store,
    get_artifact,
    get_artifact_by_key,
    list_artifacts,
    list_stage_artifacts,
    put_artifact,
)
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    VALID_EVENT_TYPES,
    create_pipeline_run,
)
from app.services.pipeline_orchestrator import (
    create_orchestrator,
    execute_stage,
    get_default_handlers,
    run_pipeline_with_handlers,
)


# ── Helper factories ────────────────────────────────────────────

def _make_step4_artifacts(
    run_id: str,
    store: dict,
    engine_keys: list[str] | None = None,
    *,
    eligible_keys: set[str] | None = None,
    failed_keys: set[str] | None = None,
    empty_data_keys: set[str] | None = None,
):
    """Populate a store with Step 4-style artifacts for testing.

    Creates per-engine market_engine_output artifacts and a
    market_stage_summary artifact matching Step 4's output shape.
    """
    if engine_keys is None:
        engine_keys = ["breadth_participation", "volatility_options", "news_sentiment"]
    if eligible_keys is None:
        eligible_keys = set(engine_keys)
    if failed_keys is None:
        failed_keys = set()
    if empty_data_keys is None:
        empty_data_keys = set()

    engine_summaries = {}
    artifact_refs = {}

    for key in engine_keys:
        status = "failed" if key in failed_keys else "success"
        eligible = key in eligible_keys and key not in failed_keys

        # Write per-engine artifact
        data = {} if key in empty_data_keys else {
            "engine_result": {
                "score": 72,
                "label": "Constructive",
                "confidence_score": 80,
                "signal_quality": "medium",
            },
            "data_quality": {"overall": "good"},
            "compute_duration_s": 1.5,
            "as_of": "2026-03-11T10:00:00Z",
        }

        if key not in failed_keys:
            art = build_artifact_record(
                run_id=run_id,
                stage_key="market_data",
                artifact_key=f"engine_{key}",
                artifact_type="market_engine_output",
                data=data,
                summary={"score": 72, "label": "Constructive"},
                metadata={"engine_key": key},
            )
            put_artifact(store, art, overwrite=True)
            artifact_refs[key] = art["artifact_id"]
        else:
            artifact_refs[key] = None

        engine_summaries[key] = {
            "status": status,
            "score": 72 if status == "success" else None,
            "label": "Constructive" if status == "success" else None,
            "confidence": 80 if status == "success" else None,
            "elapsed_ms": 150,
            "artifact_ref": artifact_refs.get(key),
            "eligible_for_model_analysis": eligible,
        }

    # Write stage summary
    summary_data = {
        "stage_key": "market_data",
        "stage_status": "success",
        "total_attempted": len(engine_keys),
        "engines_succeeded": [k for k in engine_keys if k not in failed_keys],
        "engines_failed": [k for k in engine_keys if k in failed_keys],
        "engines_skipped": [],
        "engines_unavailable": [],
        "success_count": len(engine_keys) - len(failed_keys),
        "fail_count": len(failed_keys),
        "artifact_refs": artifact_refs,
        "engine_summaries": engine_summaries,
    }
    summary_art = build_artifact_record(
        run_id=run_id,
        stage_key="market_data",
        artifact_key="market_stage_summary",
        artifact_type="market_stage_summary",
        data=summary_data,
        summary={"stage_status": "success"},
    )
    put_artifact(store, summary_art, overwrite=True)
    return engine_summaries


def _mock_model_executor(engine_key, model_input):
    """Mock model executor that returns a simple result."""
    return {
        "label": "Constructive",
        "score": 75,
        "confidence": 0.8,
        "summary": f"Model analysis for {engine_key}",
        "key_points": [f"Point 1 for {engine_key}"],
        "risks": [f"Risk 1 for {engine_key}"],
    }


def _failing_model_executor(engine_key, model_input):
    """Mock model executor that always fails."""
    raise RuntimeError(f"Model call failed for {engine_key}")


def _selective_model_executor(fail_keys=None):
    """Return a model executor that fails for specific engine keys."""
    fail_keys = fail_keys or set()
    def _executor(engine_key, model_input):
        if engine_key in fail_keys:
            raise RuntimeError(f"Model call failed for {engine_key}")
        return {
            "label": "OK",
            "score": 70,
            "summary": f"Analysis for {engine_key}",
        }
    return _executor


def _success_handler(run, artifact_store, stage_key, **kwargs):
    """Handler that always succeeds (for orchestrator tests)."""
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 5},
        "artifacts": [],
        "metadata": {"test": True},
        "error": None,
    }


def _make_run_and_store():
    """Create a fresh run and artifact store for testing."""
    run = create_pipeline_run(run_id="test-run-001")
    store = create_artifact_store(run["run_id"])
    return run, store


# =====================================================================
#  Test: Model Analysis Record Shape
# =====================================================================

class TestModelAnalysisRecord:
    """build_model_analysis_record produces correct shape."""

    def test_analyzed_record_shape(self):
        rec = build_model_analysis_record(
            engine_key="breadth_participation",
            status="analyzed",
            source_artifact_ref="art-123",
            started_at="2026-03-11T10:00:00Z",
            completed_at="2026-03-11T10:00:01Z",
            elapsed_ms=1000,
            model_provider="local",
            model_name="qwen-32b",
            normalized_output_ref="art-456",
            downstream_usable=True,
        )
        assert rec["engine_key"] == "breadth_participation"
        assert rec["status"] == "analyzed"
        assert rec["source_artifact_ref"] == "art-123"
        assert rec["started_at"] == "2026-03-11T10:00:00Z"
        assert rec["completed_at"] == "2026-03-11T10:00:01Z"
        assert rec["elapsed_ms"] == 1000
        assert rec["model_provider"] == "local"
        assert rec["model_name"] == "qwen-32b"
        assert rec["normalized_output_ref"] == "art-456"
        assert rec["downstream_usable"] is True
        assert rec["error"] is None

    def test_failed_record_with_error(self):
        err = {"code": "TEST", "message": "fail"}
        rec = build_model_analysis_record(
            engine_key="news_sentiment",
            status="failed",
            error=err,
        )
        assert rec["status"] == "failed"
        assert rec["error"] == err
        assert rec["downstream_usable"] is False

    def test_skipped_record_defaults(self):
        rec = build_model_analysis_record(
            engine_key="volatility_options",
            status="skipped_not_eligible",
        )
        assert rec["started_at"] is None
        assert rec["completed_at"] is None
        assert rec["elapsed_ms"] is None
        assert rec["model_provider"] is None
        assert rec["normalized_input_ref"] is None
        assert rec["normalized_output_ref"] is None

    def test_all_fields_present(self):
        rec = build_model_analysis_record(
            engine_key="test",
            status="analyzed",
        )
        expected_fields = {
            "engine_key", "status", "source_artifact_ref",
            "started_at", "completed_at", "elapsed_ms",
            "model_provider", "model_name",
            "normalized_input_ref", "normalized_output_ref",
            "error", "downstream_usable",
        }
        assert set(rec.keys()) == expected_fields


# =====================================================================
#  Test: Status Vocabulary
# =====================================================================

class TestStatusVocabulary:
    """ANALYSIS_STATUSES covers all expected states."""

    def test_expected_statuses(self):
        expected = {
            "analyzed",
            "skipped_not_eligible",
            "skipped_missing_artifact",
            "skipped_invalid_payload",
            "skipped_disabled",
            "failed",
        }
        assert ANALYSIS_STATUSES == expected

    def test_event_types_registered(self):
        assert "model_analysis_started" in VALID_EVENT_TYPES
        assert "model_analysis_completed" in VALID_EVENT_TYPES
        assert "model_analysis_failed" in VALID_EVENT_TYPES


# =====================================================================
#  Test: Eligibility Logic
# =====================================================================

class TestEligibility:
    """_determine_eligibility classifies engines correctly."""

    def test_eligible_engine(self):
        summary_entry = {"eligible_for_model_analysis": True, "status": "success"}
        artifact = {"data": {"engine_result": {"score": 72}}, "artifact_id": "art-1"}
        status, reason = _determine_eligibility("breadth", summary_entry, artifact)
        assert status == "eligible"
        assert reason == ""

    def test_not_eligible_flag(self):
        summary_entry = {"eligible_for_model_analysis": False, "status": "success"}
        artifact = {"data": {"engine_result": {}}, "artifact_id": "art-1"}
        status, reason = _determine_eligibility("breadth", summary_entry, artifact)
        assert status == "skipped_not_eligible"
        assert "not marked eligible" in reason

    def test_missing_summary_entry(self):
        artifact = {"data": {"engine_result": {}}, "artifact_id": "art-1"}
        status, reason = _determine_eligibility("breadth", None, artifact)
        assert status == "skipped_not_eligible"

    def test_missing_artifact(self):
        summary_entry = {"eligible_for_model_analysis": True, "status": "success"}
        status, reason = _determine_eligibility("breadth", summary_entry, None)
        assert status == "skipped_missing_artifact"

    def test_empty_data(self):
        summary_entry = {"eligible_for_model_analysis": True, "status": "success"}
        artifact = {"data": {}, "artifact_id": "art-1"}
        status, reason = _determine_eligibility("breadth", summary_entry, artifact)
        assert status == "skipped_invalid_payload"

    def test_null_data(self):
        summary_entry = {"eligible_for_model_analysis": True, "status": "success"}
        artifact = {"data": None, "artifact_id": "art-1"}
        status, reason = _determine_eligibility("breadth", summary_entry, artifact)
        assert status == "skipped_invalid_payload"

    def test_disabled_engine(self):
        summary_entry = {"eligible_for_model_analysis": True, "status": "success"}
        artifact = {"data": {"engine_result": {}}, "artifact_id": "art-1"}
        status, reason = _determine_eligibility(
            "breadth", summary_entry, artifact,
            disabled_engines={"breadth"},
        )
        assert status == "skipped_disabled"

    def test_disabled_takes_priority(self):
        """Disabled check happens before eligible check."""
        summary_entry = {"eligible_for_model_analysis": True, "status": "success"}
        artifact = {"data": {"engine_result": {}}, "artifact_id": "art-1"}
        status, _ = _determine_eligibility(
            "breadth", summary_entry, artifact,
            disabled_engines={"breadth"},
        )
        assert status == "skipped_disabled"


# =====================================================================
#  Test: Engine Normalization
# =====================================================================

class TestEngineNormalization:
    """normalize_engine_for_model produces stable model-ready input."""

    def test_output_shape(self):
        artifact = {
            "artifact_id": "art-123",
            "data": {
                "engine_result": {
                    "score": 72,
                    "label": "Constructive",
                    "confidence_score": 80,
                },
                "data_quality": {"overall": "good"},
                "as_of": "2026-03-11T10:00:00Z",
            },
        }
        result = normalize_engine_for_model("breadth_participation", artifact)
        assert result["engine_key"] == "breadth_participation"
        assert result["engine_name"] == "Breadth & Participation"
        assert result["source_artifact_ref"] == "art-123"
        assert isinstance(result["normalized_data"], dict)
        assert isinstance(result["compact_summary"], dict)
        assert isinstance(result["warnings"], list)

    def test_compact_summary_fields(self):
        artifact = {
            "artifact_id": "art-123",
            "data": {
                "engine_result": {
                    "score": 72,
                    "label": "Constructive",
                    "confidence_score": 80,
                    "signal_quality": "medium",
                },
                "data_quality": {"overall": "good"},
                "as_of": "2026-03-11T10:00:00Z",
            },
        }
        result = normalize_engine_for_model("breadth_participation", artifact)
        cs = result["compact_summary"]
        assert "score" in cs
        assert "label" in cs
        assert "confidence" in cs
        assert "signal_quality" in cs

    def test_unknown_engine_key(self):
        artifact = {
            "artifact_id": "art-x",
            "data": {"engine_result": {"score": 50}},
        }
        result = normalize_engine_for_model("unknown_engine", artifact)
        assert result["engine_key"] == "unknown_engine"
        assert result["engine_name"] == "unknown_engine"

    def test_empty_data_still_normalizes(self):
        artifact = {
            "artifact_id": "art-x",
            "data": {},
        }
        result = normalize_engine_for_model("breadth_participation", artifact)
        assert result["engine_key"] == "breadth_participation"
        assert isinstance(result["normalized_data"], dict)


# =====================================================================
#  Test: Stage Summary Builder
# =====================================================================

class TestStageSummary:
    """build_model_stage_summary produces correct rollup."""

    def test_all_analyzed(self):
        records = {
            "breadth": {
                "record": build_model_analysis_record(
                    engine_key="breadth", status="analyzed",
                    normalized_output_ref="art-1", downstream_usable=True,
                ),
                "model_output": {},
            },
            "volatility": {
                "record": build_model_analysis_record(
                    engine_key="volatility", status="analyzed",
                    normalized_output_ref="art-2", downstream_usable=True,
                ),
                "model_output": {},
            },
        }
        summary = build_model_stage_summary(records, {}, elapsed_ms=500)
        assert summary["stage_status"] == "success"
        assert summary["analyzed_count"] == 2
        assert summary["failed_count"] == 0
        assert summary["skipped_count"] == 0
        assert set(summary["engines_analyzed"]) == {"breadth", "volatility"}
        assert summary["elapsed_ms"] == 500

    def test_mixed_success_failure(self):
        records = {
            "breadth": {
                "record": build_model_analysis_record(
                    engine_key="breadth", status="analyzed",
                    downstream_usable=True,
                ),
                "model_output": {},
            },
            "volatility": {
                "record": build_model_analysis_record(
                    engine_key="volatility", status="failed",
                ),
                "model_output": None,
            },
        }
        summary = build_model_stage_summary(records, {})
        assert summary["stage_status"] == "degraded"
        assert summary["analyzed_count"] == 1
        assert summary["failed_count"] == 1

    def test_all_failed(self):
        records = {
            "breadth": {
                "record": build_model_analysis_record(
                    engine_key="breadth", status="failed",
                ),
                "model_output": None,
            },
        }
        summary = build_model_stage_summary(records, {})
        assert summary["stage_status"] == "failed"

    def test_no_attempts(self):
        summary = build_model_stage_summary({}, {})
        assert summary["stage_status"] == "no_eligible_inputs"
        assert summary["total_attempted"] == 0

    def test_skipped_by_reason(self):
        skipped = {
            "news": build_model_analysis_record(
                engine_key="news", status="skipped_not_eligible",
            ),
            "flows": build_model_analysis_record(
                engine_key="flows", status="skipped_missing_artifact",
            ),
        }
        summary = build_model_stage_summary({}, skipped)
        assert summary["skipped_count"] == 2
        assert "news" in summary["engines_skipped"]["skipped_not_eligible"]
        assert "flows" in summary["engines_skipped"]["skipped_missing_artifact"]

    def test_engine_summaries_present(self):
        records = {
            "breadth": {
                "record": build_model_analysis_record(
                    engine_key="breadth", status="analyzed",
                    model_provider="local", model_name="test-model",
                    source_artifact_ref="art-src",
                    normalized_output_ref="art-out",
                    downstream_usable=True,
                ),
                "model_output": {},
            },
        }
        summary = build_model_stage_summary(records, {})
        es = summary["engine_summaries"]["breadth"]
        assert es["status"] == "analyzed"
        assert es["model_provider"] == "local"
        assert es["downstream_usable"] is True

    def test_summary_has_all_expected_keys(self):
        summary = build_model_stage_summary({}, {})
        expected_keys = {
            "stage_key", "stage_status", "total_considered",
            "total_attempted", "engines_analyzed", "engines_failed",
            "engines_skipped", "analyzed_count", "failed_count",
            "skipped_count", "artifact_refs", "degraded_reasons",
            "engine_summaries", "elapsed_ms", "generated_at",
        }
        assert set(summary.keys()) == expected_keys


# =====================================================================
#  Test: All Engines Analyzed Successfully
# =====================================================================

class TestAllEnginesSuccess:
    """Handler succeeds when all eligible engines are analyzed."""

    def test_all_succeed(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert result["outcome"] == "completed"
        assert result["error"] is None
        assert result["summary_counts"]["analyses_succeeded"] == 3
        assert result["summary_counts"]["analyses_failed"] == 0
        assert result["summary_counts"]["analyses_skipped"] == 0

    def test_artifacts_written(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )

        # Per-engine artifacts
        for key in ["breadth_participation", "volatility_options", "news_sentiment"]:
            art = get_artifact_by_key(store, "market_model_analysis", f"model_{key}")
            assert art is not None
            assert art["artifact_type"] == "market_model_output"
            assert art["data"] is not None
            assert art["metadata"]["engine_key"] == key

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )

        summary_art = get_artifact_by_key(
            store, "market_model_analysis", "model_stage_summary",
        )
        assert summary_art is not None
        assert summary_art["data"]["stage_status"] == "success"
        assert summary_art["data"]["analyzed_count"] == 3

    def test_metadata_has_records(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert "analysis_records" in result["metadata"]
        assert "stage_summary_artifact_id" in result["metadata"]
        assert result["metadata"]["stage_status"] == "success"


# =====================================================================
#  Test: Partial Failure
# =====================================================================

class TestPartialFailure:
    """Mixed success/failure produces degraded stage status."""

    def test_one_fails(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        executor = _selective_model_executor(fail_keys={"news_sentiment"})
        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=executor,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["analyses_succeeded"] == 2
        assert result["summary_counts"]["analyses_failed"] == 1
        assert result["metadata"]["stage_status"] == "degraded"

    def test_degraded_reasons_populated(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        executor = _selective_model_executor(fail_keys={"news_sentiment"})
        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=executor,
        )
        assert len(result["metadata"]["degraded_reasons"]) > 0


# =====================================================================
#  Test: All Fail
# =====================================================================

class TestAllFail:
    """All eligible analyses failing produces stage failure."""

    def test_all_fail(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_failing_model_executor,
        )
        assert result["outcome"] == "failed"
        assert result["error"] is not None
        assert result["error"]["code"] == "ALL_ANALYSES_FAILED"
        assert result["summary_counts"]["analyses_succeeded"] == 0
        assert result["summary_counts"]["analyses_failed"] == 3


# =====================================================================
#  Test: Missing Step 4 Summary
# =====================================================================

class TestMissingSummary:
    """Handler fails when Step 4 summary artifact is missing."""

    def test_no_summary(self):
        run, store = _make_run_and_store()
        # No Step 4 artifacts at all

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_SOURCE_SUMMARY"


# =====================================================================
#  Test: Zero Eligible Engines
# =====================================================================

class TestZeroEligible:
    """No eligible engines produces completed with no-op status."""

    def test_none_eligible(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            eligible_keys=set(),  # none eligible
        )

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert result["outcome"] == "completed"
        assert result["error"] is None
        assert result["summary_counts"]["analyses_attempted"] == 0
        assert result["summary_counts"]["analyses_skipped"] == 3

    def test_all_disabled(self):
        run, store = _make_run_and_store()
        keys = ["breadth_participation", "volatility_options", "news_sentiment"]
        _make_step4_artifacts(run["run_id"], store, engine_keys=keys)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
            disabled_engines=set(keys),
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["analyses_skipped"] == 3


# =====================================================================
#  Test: Missing Per-Engine Artifact
# =====================================================================

class TestMissingEngineArtifact:
    """Engines with missing artifacts are skipped gracefully."""

    def test_missing_artifact_skipped(self):
        run, store = _make_run_and_store()
        # Create summary with 3 engines but only 2 have artifacts
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation", "volatility_options", "news_sentiment"],
            failed_keys={"news_sentiment"},  # no artifact written for failed
        )
        # Make news_sentiment still eligible in summary but artifact is missing
        summary_art = get_artifact_by_key(store, "market_data", "market_stage_summary")
        summary_art["data"]["engine_summaries"]["news_sentiment"]["eligible_for_model_analysis"] = True

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["analyses_succeeded"] == 2
        assert result["summary_counts"]["analyses_skipped"] >= 1


# =====================================================================
#  Test: Invalid / Thin Payload
# =====================================================================

class TestInvalidPayload:
    """Engines with empty/invalid data are skipped."""

    def test_empty_data_skipped(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation", "volatility_options"],
            empty_data_keys={"breadth_participation"},
        )

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert result["summary_counts"]["analyses_succeeded"] == 1
        assert result["summary_counts"]["analyses_skipped"] >= 1


# =====================================================================
#  Test: Bounded Parallel Execution
# =====================================================================

class TestBoundedParallel:
    """Parallel execution respects max_workers."""

    def test_custom_max_workers(self):
        run, store = _make_run_and_store()
        keys = [
            "breadth_participation", "volatility_options",
            "news_sentiment", "cross_asset_macro",
        ]
        _make_step4_artifacts(run["run_id"], store, engine_keys=keys)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
            max_workers=1,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["analyses_succeeded"] == 4

    def test_default_max_workers_value(self):
        assert DEFAULT_MODEL_MAX_WORKERS == 2

    def test_parallel_with_failures(self):
        run, store = _make_run_and_store()
        keys = [
            "breadth_participation", "volatility_options",
            "news_sentiment", "cross_asset_macro",
        ]
        _make_step4_artifacts(run["run_id"], store, engine_keys=keys)

        executor = _selective_model_executor(
            fail_keys={"volatility_options", "cross_asset_macro"},
        )
        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=executor,
            max_workers=3,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["analyses_succeeded"] == 2
        assert result["summary_counts"]["analyses_failed"] == 2


# =====================================================================
#  Test: Event Emission
# =====================================================================

class TestEventEmission:
    """Structured events emitted during model analysis."""

    def test_events_emitted(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation"],
        )

        events = []
        def capture_event(event):
            events.append(event)

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
            event_callback=capture_event,
        )

        event_types = [e["event_type"] for e in events]
        assert "model_analysis_started" in event_types
        assert "model_analysis_completed" in event_types

    def test_failure_event_emitted(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation"],
        )

        events = []
        def capture_event(event):
            events.append(event)

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_failing_model_executor,
            event_callback=capture_event,
        )

        event_types = [e["event_type"] for e in events]
        assert "model_analysis_started" in event_types
        assert "model_analysis_failed" in event_types

    def test_events_have_engine_key(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation"],
        )

        events = []
        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
            event_callback=lambda e: events.append(e),
        )

        for e in events:
            assert "engine_key" in e.get("metadata", {})


# =====================================================================
#  Test: Model Results Override
# =====================================================================

class TestModelResultsOverride:
    """model_results_override bypasses real model execution."""

    def test_override_used(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        overrides = {
            "breadth_participation": {"label": "Override", "score": 99},
            "volatility_options": {"label": "Override2", "score": 88},
            "news_sentiment": {"label": "Override3", "score": 77},
        }

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_results_override=overrides,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["analyses_succeeded"] == 3

        # Verify override data persisted
        art = get_artifact_by_key(
            store, "market_model_analysis", "model_breadth_participation",
        )
        assert art["data"]["score"] == 99

    def test_partial_override(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        overrides = {
            "breadth_participation": {"label": "Override", "score": 99},
        }

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_results_override=overrides,
        )
        # Only breadth gets analyzed; others skipped from override
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["analyses_succeeded"] >= 1


# =====================================================================
#  Test: Handler Contract Shape
# =====================================================================

class TestHandlerContract:
    """Handler returns orchestrator-compatible result shape."""

    def test_required_keys(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert "outcome" in result
        assert "summary_counts" in result
        assert "artifacts" in result
        assert "metadata" in result
        assert "error" in result

    def test_summary_counts_fields(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        sc = result["summary_counts"]
        assert "analyses_attempted" in sc
        assert "analyses_succeeded" in sc
        assert "analyses_failed" in sc
        assert "analyses_skipped" in sc

    def test_outcome_is_completed_or_failed(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        r1 = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert r1["outcome"] in ("completed", "failed")

        run2, store2 = _make_run_and_store()
        r2 = market_model_stage_handler(
            run2, store2, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        assert r2["outcome"] in ("completed", "failed")


# =====================================================================
#  Test: Artifact Lineage
# =====================================================================

class TestArtifactLineage:
    """Artifacts have proper lineage metadata."""

    def test_model_output_lineage(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation"],
        )

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )

        art = get_artifact_by_key(
            store, "market_model_analysis", "model_breadth_participation",
        )
        assert art["run_id"] == run["run_id"]
        assert art["stage_key"] == "market_model_analysis"
        assert art["metadata"]["engine_key"] == "breadth_participation"
        assert art["metadata"]["source_artifact_ref"] is not None

    def test_summary_lineage(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )

        art = get_artifact_by_key(
            store, "market_model_analysis", "model_stage_summary",
        )
        assert art["run_id"] == run["run_id"]
        assert art["stage_key"] == "market_model_analysis"


# =====================================================================
#  Test: Orchestrator Integration
# =====================================================================

class TestOrchestratorIntegration:
    """market_model_stage_handler wired correctly in orchestrator."""

    def test_handler_registered(self):
        handlers = get_default_handlers()
        assert "market_model_analysis" in handlers
        from app.services.pipeline_market_model_stage import market_model_stage_handler as h
        assert handlers["market_model_analysis"] is h

    def test_stage_executes_in_pipeline(self):
        """Stage runs when market_data completes and provides artifacts."""

        def _market_data_handler(run, artifact_store, stage_key, **kwargs):
            """Simulate market_data stage writing artifacts."""
            _make_step4_artifacts(run["run_id"], artifact_store)
            return {
                "outcome": "completed",
                "summary_counts": {"engines_succeeded": 3},
                "artifacts": [],
                "metadata": {},
                "error": None,
            }

        def _model_executor(engine_key, model_input):
            return {"label": "Test", "score": 70}

        result = run_pipeline_with_handlers(
            {
                "market_data": _market_data_handler,
                "market_model_analysis": lambda r, s, sk, **kw: market_model_stage_handler(
                    r, s, sk, model_executor=_model_executor, **kw
                ),
            },
        )
        stage_results = result["stage_results"]
        model_stage = [
            sr for sr in stage_results
            if sr["stage_key"] == "market_model_analysis"
        ]
        assert len(model_stage) == 1
        assert model_stage[0]["outcome"] == "completed"

    def test_skipped_when_market_data_fails(self):
        """model_analysis skipped if market_data fails (dependency)."""
        def _failing_market_handler(run, artifact_store, stage_key, **kwargs):
            return {
                "outcome": "failed",
                "summary_counts": {},
                "artifacts": [],
                "metadata": {},
                "error": {
                    "code": "MARKET_FAIL",
                    "message": "Market data failed",
                    "source": stage_key,
                    "detail": {},
                    "timestamp": "2026-03-11T00:00:00Z",
                    "retryable": False,
                },
            }

        result = run_pipeline_with_handlers(
            {"market_data": _failing_market_handler},
        )
        stage_results = result["stage_results"]
        model_stage = [
            sr for sr in stage_results
            if sr["stage_key"] == "market_model_analysis"
        ]
        # Should be skipped due to halted pipeline (market_data is fatal)
        assert len(model_stage) == 1
        assert model_stage[0]["outcome"] == "skipped"


# =====================================================================
#  Test: Step 4 Artifact Compatibility
# =====================================================================

class TestStep4Compatibility:
    """Stage correctly retrieves Step 4 artifacts via published seams."""

    def test_retrieves_stage_summary(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        art = _get_market_stage_summary(store)
        assert art is not None
        assert art["artifact_type"] == "market_stage_summary"
        assert "engine_summaries" in art["data"]

    def test_retrieves_engine_artifacts(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        for key in ["breadth_participation", "volatility_options", "news_sentiment"]:
            art = _get_engine_artifact(store, key)
            assert art is not None
            assert art["artifact_type"] == "market_engine_output"
            assert art["metadata"]["engine_key"] == key


# =====================================================================
#  Test: Output Shape Stability
# =====================================================================

class TestOutputStability:
    """Handler output shape is stable and complete."""

    def test_metadata_keys(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        meta = result["metadata"]
        assert "stage_summary_artifact_id" in meta
        assert "model_artifact_ids" in meta
        assert "stage_status" in meta
        assert "elapsed_ms" in meta
        assert "analysis_records" in meta
        assert "degraded_reasons" in meta

    def test_analysis_record_in_metadata(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation"],
        )

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )
        records = result["metadata"]["analysis_records"]
        assert "breadth_participation" in records
        rec = records["breadth_participation"]
        assert rec["status"] == "analyzed"
        assert rec["downstream_usable"] is True


# =====================================================================
#  Test: Step 5 Forward Compatibility
# =====================================================================

class TestForwardCompatibility:
    """Downstream stages can consume Step 5 artifacts reliably."""

    def test_model_output_retrievable_by_key(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )

        for key in ["breadth_participation", "volatility_options", "news_sentiment"]:
            art = get_artifact_by_key(
                store, "market_model_analysis", f"model_{key}",
            )
            assert art is not None
            assert art["data"] is not None

    def test_summary_retrievable_by_key(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )

        art = get_artifact_by_key(
            store, "market_model_analysis", "model_stage_summary",
        )
        assert art is not None
        data = art["data"]
        assert "engines_analyzed" in data
        assert "engine_summaries" in data

    def test_engine_key_mapping_stable(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(run["run_id"], store)

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_mock_model_executor,
        )

        summary = get_artifact_by_key(
            store, "market_model_analysis", "model_stage_summary",
        )
        es = summary["data"]["engine_summaries"]
        for key in ["breadth_participation", "volatility_options", "news_sentiment"]:
            assert key in es
            assert "status" in es[key]
            assert "downstream_usable" in es[key]


# =====================================================================
#  Test: Executor Exception Handling
# =====================================================================

class TestExecutorExceptions:
    """Model executor exceptions are captured per-engine."""

    def test_exception_captured(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation"],
        )

        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=_failing_model_executor,
        )
        records = result["metadata"]["analysis_records"]
        rec = records["breadth_participation"]
        assert rec["status"] == "failed"
        assert rec["error"] is not None
        assert rec["error"]["code"] == "MODEL_ANALYSIS_EXCEPTION"

    def test_exception_does_not_crash_stage(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation", "volatility_options"],
        )

        executor = _selective_model_executor(fail_keys={"breadth_participation"})
        result = market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=executor,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["analyses_succeeded"] == 1
        assert result["summary_counts"]["analyses_failed"] == 1


# =====================================================================
#  Test: Model Executor Injection
# =====================================================================

class TestExecutorInjection:
    """Mock executor can be injected for testing."""

    def test_custom_executor_called(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation"],
        )

        calls = []
        def tracking_executor(engine_key, model_input):
            calls.append(engine_key)
            return {"label": "Tracked", "score": 42}

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=tracking_executor,
        )
        assert "breadth_participation" in calls

    def test_executor_receives_model_input(self):
        run, store = _make_run_and_store()
        _make_step4_artifacts(
            run["run_id"], store,
            engine_keys=["breadth_participation"],
        )

        received = []
        def capturing_executor(engine_key, model_input):
            received.append((engine_key, model_input))
            return {"label": "OK"}

        market_model_stage_handler(
            run, store, "market_model_analysis",
            model_executor=capturing_executor,
        )
        assert len(received) == 1
        key, mi = received[0]
        assert key == "breadth_participation"
        assert "engine_key" in mi
        assert "normalized_data" in mi
        assert "compact_summary" in mi
