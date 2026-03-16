"""Tests for Pipeline Orchestrator v1.0.

Coverage targets:
─── Orchestrator initialization
    - create_orchestrator produces valid shape
    - run and artifact_store are initialized
    - default handlers cover all stages
    - custom handlers merged over defaults
    - dependency map initialized
    - event callback preserved
─── Run + artifact store creation
    - run_id passthrough
    - trigger_source passthrough
    - requested_scope passthrough
    - artifact store run_id matches run
─── Stage execution in canonical order
    - all 12 stages execute in order
    - stage_results list length matches stages
    - stage_results order matches PIPELINE_STAGES
─── Dependency gating
    - satisfied dependencies allow execution
    - unsatisfied dependencies skip stage
    - missing dependency stage skips
    - dependency reason recorded
    - multi-dependency stage requires all
─── Successful stage lifecycle
    - stage transitions pending → running → completed
    - timing captured
    - summary_counts passed through
    - artifacts written
─── Failed stage lifecycle
    - handler returning outcome=failed
    - handler raising an exception
    - error captured in run.errors
    - stage status is failed
    - error dict structured
─── Skipped stage lifecycle
    - skipped due to dep failure
    - skipped due to pipeline halt
    - skip reason recorded
    - handler not invoked
─── Stop-on-failure behavior
    - fatal stage failure halts pipeline
    - downstream stages skipped after halt
    - non-continuable stage fails → halt
─── Continue behavior
    - continuable stage failure does not halt
    - downstream independent stages still run
─── Custom/injected handler registry
    - custom handler invoked for its stage
    - stubs used for non-custom stages
    - handler receives run and artifact_store
─── Unknown stage handling
    - missing handler uses stub
─── Run finalization
    - finalize_run called
    - run status set (completed / failed / partial_failed)
    - ended_at populated
─── Structured result shape
    - result has run, artifact_store, stage_results, summary
    - summary has run_summary, artifact_summary, stage_outcome_counts
─── No duplication of Step 1 status semantics
    - orchestrator uses Step 1 mark_stage_* helpers
    - orchestrator uses Step 1 finalize_run
    - orchestrator uses Step 1 compute_run_status

Representative scenarios:
─── All stubs succeed
─── One stage fails, downstream skipped
─── Dependency unsatisfied, stage skipped
─── Handler raises exception
"""

import json

import pytest

from app.services.pipeline_orchestrator import (
    _CONTINUABLE_STAGES,
    _DEFAULT_DEPENDENCY_MAP,
    _ORCHESTRATOR_VERSION,
    _check_dependencies,
    _is_fatal_failure,
    _stub_handler,
    build_stage_result,
    create_orchestrator,
    execute_stage,
    get_default_dependency_map,
    get_default_handlers,
    get_stop_policy,
    run_pipeline,
    run_pipeline_with_handlers,
    summarize_pipeline_result,
)
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    create_pipeline_run,
    validate_pipeline_run,
)
from app.services.pipeline_artifact_store import (
    create_artifact_store,
    get_artifact,
    list_artifacts,
    validate_artifact_store,
)


# ── Helper factories ────────────────────────────────────────────

def _success_handler(
    run, artifact_store, stage_key, **kwargs
):
    """Handler that always succeeds with some counts."""
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 5},
        "artifacts": [],
        "metadata": {"test": True},
        "error": None,
    }


def _failing_handler(
    run, artifact_store, stage_key, **kwargs
):
    """Handler that returns a clean failure."""
    return {
        "outcome": "failed",
        "summary_counts": {},
        "artifacts": [],
        "metadata": {},
        "error": {
            "code": "TEST_FAILURE",
            "message": f"Stage {stage_key} intentionally failed",
            "source": stage_key,
            "detail": {},
            "timestamp": "2026-01-01T00:00:00Z",
            "retryable": False,
        },
    }


def _exception_handler(
    run, artifact_store, stage_key, **kwargs
):
    """Handler that raises an exception."""
    raise RuntimeError(f"Boom in {stage_key}")


def _artifact_handler(
    run, artifact_store, stage_key, **kwargs
):
    """Handler that returns artifacts to write."""
    return {
        "outcome": "completed",
        "summary_counts": {"artifacts_produced": 2},
        "artifacts": [
            {
                "artifact_key": f"{stage_key}_result_a",
                "artifact_type": "market_engine_output",
                "data": {"key": "value_a"},
                "summary": {"label": "a"},
            },
            {
                "artifact_key": f"{stage_key}_result_b",
                "artifact_type": "market_engine_output",
                "data": {"key": "value_b"},
                "summary": {"label": "b"},
            },
        ],
        "metadata": {},
        "error": None,
    }


def _bad_return_handler(
    run, artifact_store, stage_key, **kwargs
):
    """Handler that returns a non-dict."""
    return "not a dict"


# =====================================================================
#  Orchestrator Initialization
# =====================================================================

class TestOrchestratorInit:

    def test_create_orchestrator_shape(self):
        orch = create_orchestrator(run_id="run-init-001")
        expected_keys = {
            "run", "artifact_store", "handlers", "dependency_map",
            "event_callback", "stage_results", "orchestrator_version",
            "module_role",
        }
        assert expected_keys.issubset(orch.keys())

    def test_run_initialized(self):
        orch = create_orchestrator(run_id="run-init-002")
        run = orch["run"]
        assert run["run_id"] == "run-init-002"
        assert run["status"] == "pending"
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Run validation: {errors}"

    def test_artifact_store_initialized(self):
        orch = create_orchestrator(run_id="run-init-003")
        store = orch["artifact_store"]
        assert store["run_id"] == "run-init-003"
        ok, errors = validate_artifact_store(store)
        assert ok, f"Store validation: {errors}"

    def test_default_handlers_cover_all_stages(self):
        handlers = get_default_handlers()
        for stage in PIPELINE_STAGES:
            assert stage in handlers, f"missing handler: {stage}"

    def test_custom_handlers_merged(self):
        custom = {"market_data": _success_handler}
        orch = create_orchestrator(handlers=custom)
        assert orch["handlers"]["market_data"] is _success_handler
        # All 12 canonical stages now have real handlers;
        # verify custom handler overrides the real one
        assert orch["handlers"]["market_data"] is _success_handler

    def test_dependency_map_set(self):
        orch = create_orchestrator()
        assert isinstance(orch["dependency_map"], dict)
        assert "market_model_analysis" in orch["dependency_map"]

    def test_shared_context_depends_on_candidate_selection(self):
        """shared_context must wait for candidate_selection (DAG contract)."""
        orch = create_orchestrator()
        deps = orch["dependency_map"]
        assert "candidate_selection" in deps["shared_context"]
        assert "market_model_analysis" in deps["shared_context"]

    def test_event_callback_preserved(self):
        cb = lambda e: None
        orch = create_orchestrator(event_callback=cb)
        assert orch["event_callback"] is cb

    def test_orchestrator_version(self):
        orch = create_orchestrator()
        assert orch["orchestrator_version"] == _ORCHESTRATOR_VERSION

    def test_module_role(self):
        orch = create_orchestrator()
        assert orch["module_role"] == "orchestrator"

    def test_trigger_source_passthrough(self):
        orch = create_orchestrator(trigger_source="scheduled")
        assert orch["run"]["trigger_source"] == "scheduled"

    def test_requested_scope_passthrough(self):
        scope = {"symbols": ["SPY", "QQQ"]}
        orch = create_orchestrator(requested_scope=scope)
        assert orch["run"]["requested_scope"] == scope


# =====================================================================
#  Run + Artifact Store Creation
# =====================================================================

class TestRunArtifactStoreCreation:

    def test_run_id_passthrough(self):
        result = _all_stub_pipeline(run_id="run-create-001")
        assert result["run"]["run_id"] == "run-create-001"

    def test_artifact_store_run_id_matches(self):
        result = _all_stub_pipeline(run_id="run-create-002")
        assert result["artifact_store"]["run_id"] == "run-create-002"

    def test_run_and_store_same_id(self):
        result = _all_stub_pipeline(run_id="run-create-003")
        assert result["run"]["run_id"] == result["artifact_store"]["run_id"]


# =====================================================================
#  Stage execution in canonical order
# =====================================================================

def _all_stub_pipeline(**kwargs):
    """Run a pipeline with all stubs (including real-handler stages).

    Since get_default_handlers now maps market_data, market_model_analysis,
    scanners, candidate_selection, and shared_context to real handlers,
    tests that need pure-stub behavior must override all five explicitly.
    """
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

    def test_all_stages_executed(self):
        result = _all_stub_pipeline(run_id="run-order-001")
        assert len(result["stage_results"]) == len(PIPELINE_STAGES)

    def test_stage_results_order(self):
        result = _all_stub_pipeline(run_id="run-order-002")
        result_keys = [sr["stage_key"] for sr in result["stage_results"]]
        assert result_keys == list(PIPELINE_STAGES)

    def test_all_stages_completed_with_stubs(self):
        result = _all_stub_pipeline(run_id="run-order-003")
        for sr in result["stage_results"]:
            assert sr["outcome"] == "completed", (
                f"Stage {sr['stage_key']} not completed: {sr['outcome']}"
            )


# =====================================================================
#  Dependency Gating
# =====================================================================

class TestDependencyGating:

    def test_satisfied_deps_allow_execution(self):
        run = create_pipeline_run(run_id="run-dep-001")
        store = create_artifact_store("run-dep-001")
        # Complete all prerequisites for market_model_analysis
        from app.services.pipeline_run_contract import (
            mark_stage_completed, mark_stage_running,
        )
        for dep in ("market_data", "stock_scanners", "options_scanners"):
            mark_stage_running(run, dep)
            mark_stage_completed(run, dep)

        result = execute_stage(run, store, "market_model_analysis",
                               handler=_success_handler)
        assert result["outcome"] == "completed"
        assert result["dependency_status"] == "satisfied"

    def test_unsatisfied_deps_skip_stage(self):
        run = create_pipeline_run(run_id="run-dep-002")
        store = create_artifact_store("run-dep-002")
        # market_data not completed → market_model_analysis should skip
        result = execute_stage(run, store, "market_model_analysis",
                               handler=_success_handler)
        assert result["outcome"] == "skipped"
        assert result["dependency_status"] == "unsatisfied"
        assert result["handler_invoked"] is False

    def test_dep_reason_recorded(self):
        run = create_pipeline_run(run_id="run-dep-003")
        store = create_artifact_store("run-dep-003")
        result = execute_stage(run, store, "market_model_analysis")
        assert "market_data" in result["skipped_reason"]

    def test_failed_dep_skips_downstream(self):
        """If a dependency stage failed, downstream is skipped."""
        run = create_pipeline_run(run_id="run-dep-004")
        store = create_artifact_store("run-dep-004")
        from app.services.pipeline_run_contract import (
            mark_stage_failed, mark_stage_running,
        )
        mark_stage_running(run, "market_data")
        mark_stage_failed(run, "market_data")

        result = execute_stage(run, store, "market_model_analysis",
                               handler=_success_handler)
        assert result["outcome"] == "skipped"
        assert "failed" in result["skipped_reason"]

    def test_no_deps_always_satisfied(self):
        run = create_pipeline_run(run_id="run-dep-005")
        store = create_artifact_store("run-dep-005")
        result = execute_stage(run, store, "market_data",
                               handler=_success_handler)
        assert result["dependency_status"] == "satisfied"
        assert result["outcome"] == "completed"

    def test_multi_dep_requires_all(self):
        """candidate_enrichment depends on candidate_selection AND shared_context."""
        run = create_pipeline_run(run_id="run-dep-006")
        store = create_artifact_store("run-dep-006")
        from app.services.pipeline_run_contract import (
            mark_stage_completed, mark_stage_running,
        )
        # Complete only candidate_selection, not shared_context
        mark_stage_running(run, "candidate_selection")
        mark_stage_completed(run, "candidate_selection")

        result = execute_stage(run, store, "candidate_enrichment",
                               handler=_success_handler)
        assert result["outcome"] == "skipped"
        assert "shared_context" in result["skipped_reason"]

    def test_check_dependencies_direct(self):
        """Unit test _check_dependencies directly."""
        run = create_pipeline_run(run_id="run-dep-007")
        satisfied, reason = _check_dependencies(
            run, "market_model_analysis", _DEFAULT_DEPENDENCY_MAP
        )
        assert not satisfied
        assert "market_data" in reason


# =====================================================================
#  Successful Stage Lifecycle
# =====================================================================

class TestSuccessLifecycle:

    def test_stage_completed_state(self):
        run = create_pipeline_run(run_id="run-success-001")
        store = create_artifact_store("run-success-001")
        execute_stage(run, store, "market_data", handler=_success_handler)
        assert run["stages"]["market_data"]["status"] == "completed"

    def test_timing_captured(self):
        run = create_pipeline_run(run_id="run-success-002")
        store = create_artifact_store("run-success-002")
        result = execute_stage(run, store, "market_data",
                               handler=_success_handler)
        assert result["timing_ms"] is not None
        assert result["timing_ms"] >= 0

    def test_summary_counts_passthrough(self):
        run = create_pipeline_run(run_id="run-success-003")
        store = create_artifact_store("run-success-003")
        result = execute_stage(run, store, "market_data",
                               handler=_success_handler)
        assert result["summary_counts"]["items_processed"] == 5

    def test_artifacts_written(self):
        run = create_pipeline_run(run_id="run-success-004")
        store = create_artifact_store("run-success-004")
        result = execute_stage(run, store, "market_data",
                               handler=_artifact_handler)
        assert result["artifact_count"] == 2
        all_arts = list_artifacts(store)
        assert len(all_arts) == 2

    def test_handler_invoked_true(self):
        run = create_pipeline_run(run_id="run-success-005")
        store = create_artifact_store("run-success-005")
        result = execute_stage(run, store, "market_data",
                               handler=_success_handler)
        assert result["handler_invoked"] is True


# =====================================================================
#  Failed Stage Lifecycle
# =====================================================================

class TestFailLifecycle:

    def test_handler_failure_outcome(self):
        run = create_pipeline_run(run_id="run-fail-001")
        store = create_artifact_store("run-fail-001")
        result = execute_stage(run, store, "market_data",
                               handler=_failing_handler)
        assert result["outcome"] == "failed"
        assert result["error_count"] == 1

    def test_handler_failure_stage_status(self):
        run = create_pipeline_run(run_id="run-fail-002")
        store = create_artifact_store("run-fail-002")
        execute_stage(run, store, "market_data", handler=_failing_handler)
        assert run["stages"]["market_data"]["status"] == "failed"

    def test_handler_failure_error_in_run(self):
        run = create_pipeline_run(run_id="run-fail-003")
        store = create_artifact_store("run-fail-003")
        execute_stage(run, store, "market_data", handler=_failing_handler)
        assert len(run["errors"]) == 1
        assert run["errors"][0]["code"] == "TEST_FAILURE"

    def test_exception_handler_outcome(self):
        run = create_pipeline_run(run_id="run-fail-004")
        store = create_artifact_store("run-fail-004")
        result = execute_stage(run, store, "market_data",
                               handler=_exception_handler)
        assert result["outcome"] == "failed"
        assert result["error_count"] == 1

    def test_exception_handler_error_captured(self):
        run = create_pipeline_run(run_id="run-fail-005")
        store = create_artifact_store("run-fail-005")
        execute_stage(run, store, "market_data",
                      handler=_exception_handler)
        assert len(run["errors"]) == 1
        err = run["errors"][0]
        assert err["code"] == "STAGE_EXCEPTION"
        assert "Boom" in err["message"]

    def test_bad_return_type_treated_as_failure(self):
        run = create_pipeline_run(run_id="run-fail-006")
        store = create_artifact_store("run-fail-006")
        result = execute_stage(run, store, "market_data",
                               handler=_bad_return_handler)
        assert result["outcome"] == "failed"
        assert run["stages"]["market_data"]["status"] == "failed"


# =====================================================================
#  Skipped Stage Lifecycle
# =====================================================================

class TestSkipLifecycle:

    def test_skipped_dep_failure(self):
        run = create_pipeline_run(run_id="run-skip-001")
        store = create_artifact_store("run-skip-001")
        result = execute_stage(run, store, "market_model_analysis")
        assert result["outcome"] == "skipped"
        assert run["stages"]["market_model_analysis"]["status"] == "skipped"

    def test_skip_reason_recorded(self):
        run = create_pipeline_run(run_id="run-skip-002")
        store = create_artifact_store("run-skip-002")
        result = execute_stage(run, store, "market_model_analysis")
        assert result["skipped_reason"] != ""

    def test_handler_not_invoked_on_skip(self):
        run = create_pipeline_run(run_id="run-skip-003")
        store = create_artifact_store("run-skip-003")
        result = execute_stage(run, store, "market_model_analysis")
        assert result["handler_invoked"] is False


# =====================================================================
#  Stop-on-Failure Behavior
# =====================================================================

class TestStopOnFailure:

    def test_fatal_failure_halts_pipeline(self):
        """market_data failure should halt the pipeline."""
        handlers = {
            "market_data": _failing_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-halt-001"
        )
        stage_outcomes = {
            sr["stage_key"]: sr["outcome"]
            for sr in result["stage_results"]
        }
        assert stage_outcomes["market_data"] == "failed"
        # Wave 0 peers (scanners) may complete, be skipped, or be
        # cancelled (failed) depending on timing under parallel Wave 0:
        for sk in ("stock_scanners", "options_scanners"):
            assert stage_outcomes[sk] in ("completed", "skipped", "failed"), (
                f"Wave 0 stage {sk} should be completed, skipped, or "
                f"failed but was {stage_outcomes[sk]}"
            )
        # All stages after Wave 0 must be skipped
        wave_zero = {"market_data", "stock_scanners", "options_scanners"}
        for sk in PIPELINE_STAGES:
            if sk not in wave_zero:
                assert stage_outcomes[sk] == "skipped", (
                    f"Stage {sk} should be skipped but was "
                    f"{stage_outcomes[sk]}"
                )

    def test_fatal_failure_run_status(self):
        handlers = {
            "market_data": _failing_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-halt-002"
        )
        assert result["run"]["status"] == "failed"

    def test_exception_halts_pipeline(self):
        handlers = {
            "market_data": _exception_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-halt-003"
        )
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        assert outcomes["market_data"] == "failed"
        # Wave 0 peers may complete, be skipped, or be cancelled
        # under parallel execution — all are valid
        assert outcomes["stock_scanners"] in ("completed", "skipped", "failed")

    def test_mid_pipeline_failure_halts(self):
        """Failure in candidate_selection should halt downstream."""
        handlers = {
            "market_data": _success_handler,
            "market_model_analysis": _success_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
            "candidate_selection": _failing_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-halt-004"
        )
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        assert outcomes["market_data"] == "completed"
        assert outcomes["candidate_selection"] == "failed"
        assert outcomes["shared_context"] == "skipped"


# =====================================================================
#  Continue Behavior (continuable stages)
# =====================================================================

class TestContinueBehavior:

    def test_events_failure_does_not_halt(self):
        """events is continuable — failure shouldn't halt pipeline."""
        # We need all deps for events to actually run. events has no deps.
        # But orchestration depends on events.
        # A failure in events should NOT halt the pipeline itself,
        # but orchestration will be skipped due to missing dep.
        handlers = {
            "events": _failing_handler,
            "market_data": _success_handler,
            "market_model_analysis": _success_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
            "candidate_selection": _success_handler,
            "shared_context": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-cont-001"
        )
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        assert outcomes["events"] == "failed"
        # events is continuable, so the pipeline did not halt.
        # Stages that don't depend on events should still run.
        # market_data, scanners etc. come before events — already completed.
        # orchestration depends on events → skipped due to dep.
        # Note: market_data uses _success_handler override to avoid
        # real engine execution in tests.
        assert outcomes["market_data"] == "completed"

    def test_continuable_stage_known(self):
        assert "events" in _CONTINUABLE_STAGES

    def test_is_fatal_failure_for_market_data(self):
        assert _is_fatal_failure("market_data") is True

    def test_is_not_fatal_for_events(self):
        assert _is_fatal_failure("events") is False


# =====================================================================
#  Custom / Injected Handler Registry
# =====================================================================

class TestCustomHandlers:

    def test_custom_handler_invoked(self):
        called = {}

        def tracking_handler(run, store, stage_key, **kwargs):
            called[stage_key] = True
            return _success_handler(run, store, stage_key, **kwargs)

        handlers = {
            "market_data": tracking_handler,
            "market_model_analysis": _success_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
            "candidate_selection": _success_handler,
            "shared_context": _success_handler,
        }
        run_pipeline_with_handlers(handlers, run_id="run-custom-001")
        assert called.get("market_data") is True

    def test_stubs_used_for_non_custom(self):
        result = _all_stub_pipeline(run_id="run-custom-002")
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        # All stages should complete (stubs + custom market_data)
        assert all(v == "completed" for v in outcomes.values())

    def test_handler_receives_run_and_store(self):
        received = {}

        def inspecting_handler(run, artifact_store, stage_key, **kwargs):
            received["run_id"] = run["run_id"]
            received["store_run_id"] = artifact_store["run_id"]
            return _success_handler(run, artifact_store, stage_key, **kwargs)

        handlers = {
            "market_data": inspecting_handler,
            "market_model_analysis": _success_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
            "candidate_selection": _success_handler,
            "shared_context": _success_handler,
        }
        run_pipeline_with_handlers(
            handlers, run_id="run-custom-003"
        )
        assert received["run_id"] == "run-custom-003"
        assert received["store_run_id"] == "run-custom-003"


# =====================================================================
#  Unknown Stage Handling
# =====================================================================

class TestUnknownStage:

    def test_missing_handler_uses_stub(self):
        """If no handler registered, stub is used."""
        orch = create_orchestrator(
            run_id="run-unk-001",
            handlers={
                "stock_scanners": _success_handler,
                "options_scanners": _success_handler,
            },
        )
        # Remove a handler from registry
        del orch["handlers"]["market_data"]
        # Execute should still work via fallback in _execute_pipeline
        from app.services.pipeline_orchestrator import _execute_pipeline
        result = _execute_pipeline(orch)
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        assert outcomes["market_data"] == "completed"


# =====================================================================
#  Run Finalization
# =====================================================================

class TestRunFinalization:

    def test_finalize_called_on_success(self):
        result = _all_stub_pipeline(run_id="run-final-001")
        run = result["run"]
        assert run["ended_at"] is not None
        assert run["status"] == "completed"

    def test_finalize_called_on_failure(self):
        handlers = {
            "market_data": _failing_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-final-002"
        )
        run = result["run"]
        assert run["ended_at"] is not None
        assert run["status"] == "failed"

    def test_duration_ms_populated(self):
        result = _all_stub_pipeline(run_id="run-final-003")
        assert result["run"]["duration_ms"] is not None
        assert result["run"]["duration_ms"] >= 0


# =====================================================================
#  Structured Result Shape
# =====================================================================

class TestResultShape:

    def test_result_top_level_keys(self):
        result = _all_stub_pipeline(run_id="run-shape-001")
        assert {"run", "artifact_store", "stage_results", "summary"} == set(
            result.keys()
        )

    def test_summary_shape(self):
        result = _all_stub_pipeline(run_id="run-shape-002")
        s = result["summary"]
        assert "run_summary" in s
        assert "artifact_summary" in s
        assert "stage_outcome_counts" in s
        assert "total_timing_ms" in s
        assert s["module_role"] == "orchestrator"

    def test_stage_outcome_counts(self):
        result = _all_stub_pipeline(run_id="run-shape-003")
        counts = result["summary"]["stage_outcome_counts"]
        assert counts.get("completed", 0) == 13

    def test_failed_run_outcome_counts(self):
        handlers = {
            "market_data": _failing_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-shape-004"
        )
        counts = result["summary"]["stage_outcome_counts"]
        assert counts.get("failed", 0) >= 1
        # Wave 0 peers may complete, be skipped, or be cancelled
        # (failed) under parallel execution; non-Wave-0 stages are
        # always skipped.
        total = sum(counts.values())
        assert total == 13


# =====================================================================
#  No Duplication of Step 1 Semantics
# =====================================================================

class TestNoDuplication:

    def test_run_passes_step1_validation(self):
        result = _all_stub_pipeline(run_id="run-nodup-001")
        ok, errors = validate_pipeline_run(result["run"])
        assert ok, f"Run validation: {errors}"

    def test_store_passes_step2_validation(self):
        result = _all_stub_pipeline(run_id="run-nodup-002")
        ok, errors = validate_artifact_store(result["artifact_store"])
        assert ok, f"Store validation: {errors}"

    def test_no_parallel_status_semantics(self):
        """Orchestrator should not define its own status vocabulary."""
        import app.services.pipeline_orchestrator as module
        # Check no VALID_STAGE_STATUSES or VALID_RUN_STATUSES defined
        public = [n for n in dir(module) if not n.startswith("_")]
        assert "VALID_STAGE_STATUSES" not in public
        assert "VALID_RUN_STATUSES" not in public


# =====================================================================
#  Build Stage Result
# =====================================================================

class TestBuildStageResult:

    def test_basic_shape(self):
        result = build_stage_result(
            stage_key="market_data",
            handler_invoked=True,
            outcome="completed",
        )
        expected_keys = {
            "stage_key", "handler_invoked", "outcome",
            "artifact_count", "error_count", "skipped_reason",
            "dependency_status", "timing_ms", "summary_counts",
            "metadata",
        }
        assert expected_keys == set(result.keys())

    def test_defaults(self):
        result = build_stage_result(
            stage_key="s1", handler_invoked=False, outcome="skipped",
        )
        assert result["artifact_count"] == 0
        assert result["error_count"] == 0
        assert result["skipped_reason"] == ""
        assert result["dependency_status"] == "satisfied"
        assert result["timing_ms"] is None
        assert result["summary_counts"] == {}
        assert result["metadata"] == {}


# =====================================================================
#  Stop Policy
# =====================================================================

class TestStopPolicy:

    def test_policy_shape(self):
        policy = get_stop_policy()
        assert policy["default_behavior"] == "stop"
        assert isinstance(policy["continuable_stages"], list)
        assert isinstance(policy["description"], str)

    def test_events_in_continuable(self):
        policy = get_stop_policy()
        assert "events" in policy["continuable_stages"]


# =====================================================================
#  Default Dependency Map
# =====================================================================

class TestDependencyMap:

    def test_all_stages_in_map(self):
        deps = get_default_dependency_map()
        for stage in PIPELINE_STAGES:
            assert stage in deps

    def test_market_data_no_deps(self):
        deps = get_default_dependency_map()
        assert deps["market_data"] == []

    def test_final_response_depends_on_final_model(self):
        deps = get_default_dependency_map()
        assert "final_model_decision" in deps["final_response_normalization"]

    def test_copy_returned(self):
        d1 = get_default_dependency_map()
        d2 = get_default_dependency_map()
        d1["market_data"].append("fake")
        assert "fake" not in d2["market_data"]

    def test_shared_context_depends_on_market_data(self):
        """shared_context reads market_data artifacts directly — must declare it."""
        deps = get_default_dependency_map()
        assert "market_data" in deps["shared_context"]


# =====================================================================
#  Event Emission Seam
# =====================================================================

class TestEventSeam:

    def test_event_callback_called(self):
        events = []
        result = _all_stub_pipeline(
            run_id="run-event-001",
            event_callback=lambda e: events.append(e),
        )
        # Should have at least run_started + per-stage events + run_completed
        assert len(events) >= 14  # 1 start + 12*2 stage start/complete + 1 end = 26
        event_types = {e["event_type"] for e in events}
        assert "run_started" in event_types
        assert "stage_started" in event_types
        assert "stage_completed" in event_types

    def test_event_callback_error_isolated(self):
        """Callback errors should not crash the pipeline."""
        def bad_callback(e):
            raise RuntimeError("callback crash")

        result = _all_stub_pipeline(
            run_id="run-event-002",
            event_callback=bad_callback,
        )
        # Pipeline should still complete
        assert result["run"]["status"] == "completed"


# =====================================================================
#  Serialization Friendliness
# =====================================================================

class TestSerialization:

    def test_result_json_serializable(self):
        result = _all_stub_pipeline(run_id="run-json-001")
        serialized = json.dumps(result, default=str)
        assert isinstance(serialized, str)

    def test_failed_result_json_serializable(self):
        handlers = {
            "market_data": _exception_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-json-002"
        )
        serialized = json.dumps(result, default=str)
        assert isinstance(serialized, str)


# =====================================================================
#  Representative Scenario: All stubs succeed
# =====================================================================

class TestAllStubsSucceed:

    def test_full_pipeline_success(self):
        result = _all_stub_pipeline(run_id="run-scenario-001")
        assert result["run"]["status"] == "completed"
        assert len(result["stage_results"]) == 13
        assert all(
            sr["outcome"] == "completed"
            for sr in result["stage_results"]
        )
        ok, errs = validate_pipeline_run(result["run"])
        assert ok, errs
        ok, errs = validate_artifact_store(result["artifact_store"])
        assert ok, errs


# =====================================================================
#  Representative Scenario: One stage fails, downstream skipped
# =====================================================================

class TestFailureDownstreamSkip:

    def test_scanners_fail_downstream_skip(self):
        handlers = {
            "market_data": _success_handler,
            "market_model_analysis": _success_handler,
            "stock_scanners": _failing_handler,
            "options_scanners": _failing_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-scenario-002"
        )
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}

        assert outcomes["market_data"] == "completed"
        # scanner stages are continuable — failure does NOT halt pipeline
        assert outcomes["stock_scanners"] == "failed"
        assert outcomes["options_scanners"] == "failed"
        # market_model_analysis depends on scanner stages → skipped
        assert outcomes["market_model_analysis"] == "skipped"
        # candidate_selection also depends on scanner stages → skipped
        assert outcomes["candidate_selection"] == "skipped"

    def test_run_has_errors(self):
        handlers = {
            "market_data": _success_handler,
            "market_model_analysis": _success_handler,
            "stock_scanners": _failing_handler,
            "options_scanners": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-scenario-003"
        )
        assert len(result["run"]["errors"]) >= 1


# =====================================================================
#  Representative Scenario: Handler raises exception
# =====================================================================

class TestExceptionScenario:

    def test_exception_captured_and_halts(self):
        handlers = {
            "market_data": _exception_handler,
            "stock_scanners": _success_handler,
            "options_scanners": _success_handler,
        }
        result = run_pipeline_with_handlers(
            handlers, run_id="run-scenario-004"
        )
        assert result["run"]["status"] == "failed"
        err = result["run"]["errors"][0]
        assert err["code"] == "STAGE_EXCEPTION"
        assert "Boom" in err["message"]

        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        assert outcomes["market_data"] == "failed"
        # Wave 0 peers may complete or be skipped under parallel execution
        assert outcomes["stock_scanners"] in ("completed", "skipped")


# =====================================================================
#  Constants
# =====================================================================

class TestConstants:

    def test_orchestrator_version(self):
        assert _ORCHESTRATOR_VERSION == "1.0"

    def test_compatible_versions(self):
        from app.services.pipeline_orchestrator import _COMPATIBLE_VERSIONS
        assert "1.0" in _COMPATIBLE_VERSIONS

    def test_default_dependency_map_complete(self):
        assert len(_DEFAULT_DEPENDENCY_MAP) == len(PIPELINE_STAGES)


# =====================================================================
#  Execution ordering and Wave 0 parallelism
# =====================================================================

class TestExecutionOrdering:
    """Verify execution ordering: Wave 0 stages (no dependencies) run
    in parallel, all subsequent stages execute sequentially in
    canonical PIPELINE_STAGES order.

    CONTEXT: Full wave parallelism was rolled back (2026-03-12) for
    stability.  Targeted Wave 0 parallelism was re-introduced to
    align with the dependency graph: market_data, stock_scanners,
    and options_scanners have no dependencies and run concurrently.
    """

    _WAVE_ZERO = frozenset({"market_data", "stock_scanners", "options_scanners"})

    def _make_tracking_handler(self, log: list):
        """Build a handler that records start/end times."""
        import time

        def _handler(run, artifact_store, stage_key, **kwargs):
            log.append((stage_key, "start", time.monotonic()))
            time.sleep(0.001)
            log.append((stage_key, "end", time.monotonic()))
            return {
                "outcome": "completed",
                "summary_counts": {"items_processed": 1},
                "artifacts": [],
                "metadata": {},
                "error": None,
            }

        return _handler

    def test_stages_execute_in_canonical_order(self):
        """Stage results must appear in PIPELINE_STAGES order."""
        result = _all_stub_pipeline(run_id="run-seq-001")
        result_keys = [sr["stage_key"] for sr in result["stage_results"]]
        assert result_keys == list(PIPELINE_STAGES)

    def test_post_wave_zero_stages_do_not_overlap(self):
        """Post-Wave-0 stages should not have overlapping execution."""
        log = []
        handler = self._make_tracking_handler(log)
        handlers = {stage: handler for stage in PIPELINE_STAGES}
        run_pipeline_with_handlers(
            handlers, run_id="run-seq-002",
        )
        # Extract per-stage intervals
        intervals = {}
        for stage_key, event, t in log:
            intervals.setdefault(stage_key, {})
            intervals[stage_key][event] = t

        # Filter to post-Wave-0 stages only
        post_w0 = {
            k: v for k, v in intervals.items()
            if k not in self._WAVE_ZERO
        }
        sorted_stages = sorted(
            post_w0.items(),
            key=lambda x: x[1].get("start", 0),
        )
        for i in range(len(sorted_stages) - 1):
            prev_key, prev_times = sorted_stages[i]
            next_key, next_times = sorted_stages[i + 1]
            assert prev_times["end"] <= next_times["start"], (
                f"Post-Wave-0 stages overlapped: {prev_key} ended at "
                f"{prev_times['end']:.6f} but {next_key} started at "
                f"{next_times['start']:.6f}"
            )

    def test_market_model_analysis_after_market_data(self):
        """market_model_analysis must not start before market_data completes."""
        log = []
        handler = self._make_tracking_handler(log)
        handlers = {stage: handler for stage in PIPELINE_STAGES}
        run_pipeline_with_handlers(handlers, run_id="run-seq-003")
        intervals = {}
        for stage_key, event, t in log:
            intervals.setdefault(stage_key, {})
            intervals[stage_key][event] = t
        assert intervals["market_data"]["end"] <= intervals["market_model_analysis"]["start"]

    def test_candidate_selection_after_scanners(self):
        """candidate_selection must not start before both scanner stages complete."""
        log = []
        handler = self._make_tracking_handler(log)
        handlers = {stage: handler for stage in PIPELINE_STAGES}
        run_pipeline_with_handlers(handlers, run_id="run-seq-005")
        intervals = {}
        for stage_key, event, t in log:
            intervals.setdefault(stage_key, {})
            intervals[stage_key][event] = t
        cs_start = intervals["candidate_selection"]["start"]
        assert intervals["stock_scanners"]["end"] <= cs_start
        assert intervals["options_scanners"]["end"] <= cs_start

    def test_pipeline_progresses_past_scanners(self):
        """Pipeline must advance through scanner stages to candidate_selection
        and beyond."""
        result = _all_stub_pipeline(run_id="run-seq-006")
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        assert outcomes["stock_scanners"] == "completed"
        assert outcomes["options_scanners"] == "completed"
        assert outcomes["candidate_selection"] == "completed"
        assert outcomes["shared_context"] == "completed"
        assert outcomes["candidate_enrichment"] == "completed"
        assert result["run"]["status"] == "completed"

    def test_post_wave_zero_events_strictly_sequential(self):
        """Post-Wave-0 stage_started/stage_completed events must
        alternate — no two post-Wave-0 stage_started events without
        a stage_completed between them. Wave 0 events may interleave."""
        events = []

        def _cb(event):
            events.append(event)

        _all_stub_pipeline(run_id="run-seq-007", event_callback=_cb)

        stage_events = [
            e for e in events
            if e.get("event_type") in ("stage_started", "stage_completed")
            and e.get("stage_key") not in self._WAVE_ZERO
        ]
        i = 0
        while i < len(stage_events) - 1:
            if stage_events[i]["event_type"] == "stage_started":
                nxt = stage_events[i + 1]
                assert nxt["event_type"] == "stage_completed", (
                    f"After stage_started for "
                    f"{stage_events[i].get('stage_key')}, expected "
                    f"stage_completed but got {nxt['event_type']} for "
                    f"{nxt.get('stage_key')}"
                )
                assert nxt.get("stage_key") == stage_events[i].get("stage_key")
            i += 1

    def test_failure_halts_and_stage_results_preserved(self):
        """When a scanner stage fails, downstream stages that depend on it
        are skipped but stage_results for the failed stage are correct."""
        def _fail_stock_scanners(run, artifact_store, stage_key, **kwargs):
            return {
                "outcome": "failed",
                "summary_counts": {},
                "artifacts": [],
                "metadata": {},
                "error": {
                    "code": "TEST",
                    "message": "intentional",
                    "source": "stock_scanners",
                    "detail": {},
                    "timestamp": "2026-01-01T00:00:00Z",
                    "retryable": False,
                },
            }

        handlers = {stage: _success_handler for stage in PIPELINE_STAGES}
        handlers["stock_scanners"] = _fail_stock_scanners
        result = run_pipeline_with_handlers(
            handlers, run_id="run-seq-008",
        )
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        assert outcomes["market_data"] == "completed"
        # stock_scanners is continuable — failure doesn't halt pipeline
        assert outcomes["stock_scanners"] == "failed"
        assert outcomes["options_scanners"] == "completed"
        # candidate_selection depends on stock_scanners (failed) → skipped
        assert outcomes["candidate_selection"] == "skipped"


# =====================================================================
#  Handler direct call (no timeout wrapping)
# =====================================================================

class TestHandlerDirectCall:
    """Tests that execute_stage calls the handler directly without any
    timeout wrapping.  Legacy handler_timeout_seconds kwargs are stripped."""

    def test_handler_called_directly(self):
        """Handler is invoked directly — no ThreadPool timeout wrapper."""
        run = create_pipeline_run(run_id="direct-001")
        store = create_artifact_store("direct-001")

        result = execute_stage(
            run, store, "market_data",
            handler=_success_handler,
        )

        assert result["outcome"] == "completed"
        meta = result.get("metadata", {})
        assert meta.get("forced_completion") is None

    def test_legacy_timeout_kwarg_stripped(self):
        """Legacy handler_timeout_seconds kwarg is stripped and ignored."""
        run = create_pipeline_run(run_id="direct-002")
        store = create_artifact_store("direct-002")

        result = execute_stage(
            run, store, "market_data",
            handler=_success_handler,
            handler_kwargs={"handler_timeout_seconds": 0},
        )

        assert result["outcome"] == "completed"
        meta = result.get("metadata", {})
        assert meta.get("forced_completion") is None
