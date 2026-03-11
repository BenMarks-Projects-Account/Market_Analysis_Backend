"""Tests for Pipeline Run Contract v1.0.

Coverage targets:
─── Run initialization
    - create_pipeline_run produces valid shape
    - required keys present
    - version string locked
    - custom run_id honored
    - default trigger_source
    - requested_scope passthrough
    - metadata passthrough
─── Stage initialization and ordering
    - all canonical stages present
    - stage order matches PIPELINE_STAGES
    - each stage starts pending
    - stage labels populated
    - custom dependency map wired
    - custom stage list accepted
─── Stage transition behavior
    - pending → running
    - running → completed
    - running → failed
    - pending → skipped
    - invalid transitions rejected
    - double-start rejected
    - completed → running rejected
    - failed → running rejected
    - skipped → running rejected
    - summary_counts merged on completion
    - artifact_refs extended on completion
    - skip_reason stored
    - error attached on failure, appended to run.errors
─── Deterministic run-status rollup
    - all pending → pending
    - any running → running
    - all completed → completed
    - any failed, all terminal → failed
    - any failed, some pending → partial_failed
    - cancelled sticky
    - mixed completed + skipped → completed
    - no stages → pending
─── Failed-stage → failed-run behavior
    - single failed stage seals run as failed
    - multiple failed stages → failed
    - failed stage error in run.errors
─── Partial / incomplete run behavior
    - some completed, some pending, no failed → running
    - some completed, some failed, some pending → partial_failed
─── Structured error object shape
    - build_run_error required keys
    - retryable defaults to False
    - detail defaults to empty dict
    - source defaults to empty string
    - timestamp present
─── Structured log event shape
    - build_log_event required keys
    - event_type validation warning
    - level validation warning
    - metadata defaults to empty dict
─── Validation
    - valid run passes
    - missing key detected
    - wrong version rejected
    - invalid run status rejected
    - stages dict validated
    - stage missing key detected
    - stage invalid status detected
    - round-trip: every build output passes validation
─── Run summary
    - compact digest keys
    - module_role is "contract"
    - stage status counts accurate
─── Serialization-friendly
    - JSON-serializable
─── Finalize run
    - finalize sets ended_at and duration_ms
    - finalize computes correct status
─── Integration
    - full lifecycle: create → run stages → finalize
"""

import json

import pytest

from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    STAGE_LABELS,
    VALID_EVENT_TYPES,
    VALID_LOG_LEVELS,
    VALID_RUN_STATUSES,
    VALID_STAGE_STATUSES,
    _COMPATIBLE_VERSIONS,
    _PIPELINE_VERSION,
    _REQUIRED_ERROR_KEYS,
    _REQUIRED_LOG_EVENT_KEYS,
    _REQUIRED_RUN_KEYS,
    _REQUIRED_STAGE_KEYS,
    build_log_event,
    build_run_error,
    compute_run_status,
    create_pipeline_run,
    finalize_run,
    initialize_stage_states,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_running,
    mark_stage_skipped,
    run_summary,
    validate_pipeline_run,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _fresh_run(**overrides):
    """Build a fresh pipeline run with optional overrides."""
    kwargs = {
        "trigger_source": "test",
        "run_id": "run-test-001",
    }
    kwargs.update(overrides)
    return create_pipeline_run(**kwargs)


def _run_through_stage(run, stage_key, outcome="completed", **kwargs):
    """Move a stage through its lifecycle to the given outcome."""
    mark_stage_running(run, stage_key)
    if outcome == "completed":
        mark_stage_completed(run, stage_key, **kwargs)
    elif outcome == "failed":
        mark_stage_failed(run, stage_key, **kwargs)
    return run


# =====================================================================
#  Run initialization
# =====================================================================

class TestRunInitialization:

    def test_required_keys_present(self):
        run = _fresh_run()
        for key in _REQUIRED_RUN_KEYS:
            assert key in run, f"missing: {key}"

    def test_version_string(self):
        run = _fresh_run()
        assert run["pipeline_version"] == _PIPELINE_VERSION
        assert run["pipeline_version"] == "1.0"

    def test_custom_run_id(self):
        run = _fresh_run(run_id="custom-abc-123")
        assert run["run_id"] == "custom-abc-123"

    def test_auto_generated_run_id(self):
        run = create_pipeline_run(trigger_source="test")
        assert run["run_id"].startswith("run-")
        assert len(run["run_id"]) > 4

    def test_default_trigger_source(self):
        run = create_pipeline_run()
        assert run["trigger_source"] == "manual"

    def test_custom_trigger_source(self):
        run = _fresh_run(trigger_source="scheduled")
        assert run["trigger_source"] == "scheduled"

    def test_requested_scope_passthrough(self):
        scope = {"symbols": ["SPY", "QQQ"], "strategies": ["iron_condor"]}
        run = _fresh_run(requested_scope=scope)
        assert run["requested_scope"] == scope

    def test_requested_scope_defaults_empty(self):
        run = _fresh_run()
        assert run["requested_scope"] == {}

    def test_metadata_passthrough(self):
        meta = {"user": "test", "notes": "replay"}
        run = _fresh_run(metadata=meta)
        assert run["metadata"] == meta

    def test_metadata_defaults_empty(self):
        run = _fresh_run()
        assert run["metadata"] == {}

    def test_initial_status_pending(self):
        run = _fresh_run()
        assert run["status"] == "pending"

    def test_started_at_present(self):
        run = _fresh_run()
        assert isinstance(run["started_at"], str)
        assert len(run["started_at"]) > 0

    def test_ended_at_none(self):
        run = _fresh_run()
        assert run["ended_at"] is None

    def test_duration_ms_none(self):
        run = _fresh_run()
        assert run["duration_ms"] is None

    def test_errors_empty(self):
        run = _fresh_run()
        assert run["errors"] == []

    def test_candidate_counters_present(self):
        run = _fresh_run()
        cc = run["candidate_counters"]
        assert isinstance(cc, dict)
        for key in ("scanned", "selected", "enriched", "policy_passed",
                     "submitted_to_model", "approved", "rejected"):
            assert key in cc
            assert cc[key] == 0

    def test_log_event_counts_present(self):
        run = _fresh_run()
        lec = run["log_event_counts"]
        assert lec["total"] == 0
        assert isinstance(lec["by_level"], dict)


# =====================================================================
#  Stage initialization and ordering
# =====================================================================

class TestStageInitialization:

    def test_all_canonical_stages_present(self):
        run = _fresh_run()
        for key in PIPELINE_STAGES:
            assert key in run["stages"], f"missing stage: {key}"

    def test_stage_order_matches_pipeline_stages(self):
        run = _fresh_run()
        assert run["stage_order"] == list(PIPELINE_STAGES)

    def test_each_stage_starts_pending(self):
        run = _fresh_run()
        for key, stage in run["stages"].items():
            assert stage["status"] == "pending", f"{key} not pending"

    def test_stage_labels_populated(self):
        run = _fresh_run()
        for key, stage in run["stages"].items():
            assert stage["label"] == STAGE_LABELS[key]
            assert len(stage["label"]) > 0

    def test_stage_has_required_keys(self):
        run = _fresh_run()
        for key, stage in run["stages"].items():
            for rk in _REQUIRED_STAGE_KEYS:
                assert rk in stage, f"stage '{key}' missing: {rk}"

    def test_custom_dependency_map(self):
        deps = {"scanners": ["market_data"], "policy": ["scanners"]}
        stages = initialize_stage_states(dependency_map=deps)
        assert stages["scanners"]["depends_on"] == ["market_data"]
        assert stages["policy"]["depends_on"] == ["scanners"]
        assert stages["market_data"]["depends_on"] == []

    def test_custom_stage_list(self):
        custom = ("stage_a", "stage_b")
        stages = initialize_stage_states(custom)
        assert set(stages.keys()) == {"stage_a", "stage_b"}

    def test_stage_count(self):
        run = _fresh_run()
        assert len(run["stages"]) == len(PIPELINE_STAGES)
        assert len(PIPELINE_STAGES) == 12


# =====================================================================
#  Stage transition behavior
# =====================================================================

class TestStageTransitions:

    def test_pending_to_running(self):
        run = _fresh_run()
        mark_stage_running(run, "market_data")
        assert run["stages"]["market_data"]["status"] == "running"
        assert run["stages"]["market_data"]["started_at"] is not None

    def test_running_to_completed(self):
        run = _fresh_run()
        mark_stage_running(run, "market_data")
        mark_stage_completed(run, "market_data")
        stage = run["stages"]["market_data"]
        assert stage["status"] == "completed"
        assert stage["ended_at"] is not None
        assert stage["duration_ms"] is not None
        assert stage["duration_ms"] >= 0

    def test_running_to_failed(self):
        run = _fresh_run()
        err = build_run_error(code="TIMEOUT", message="timed out", source="market_data")
        mark_stage_running(run, "market_data")
        mark_stage_failed(run, "market_data", error=err)
        stage = run["stages"]["market_data"]
        assert stage["status"] == "failed"
        assert stage["error"] is not None
        assert stage["error"]["code"] == "TIMEOUT"

    def test_pending_to_skipped(self):
        run = _fresh_run()
        mark_stage_skipped(run, "events", reason="not needed")
        assert run["stages"]["events"]["status"] == "skipped"
        assert run["stages"]["events"]["summary_counts"]["skip_reason"] == "not needed"

    def test_invalid_pending_to_completed(self):
        run = _fresh_run()
        with pytest.raises(ValueError, match="Invalid stage transition"):
            mark_stage_completed(run, "market_data")

    def test_invalid_pending_to_failed(self):
        run = _fresh_run()
        with pytest.raises(ValueError, match="Invalid stage transition"):
            mark_stage_failed(run, "market_data")

    def test_invalid_completed_to_running(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "completed")
        with pytest.raises(ValueError, match="terminal"):
            mark_stage_running(run, "market_data")

    def test_invalid_failed_to_running(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "failed")
        with pytest.raises(ValueError, match="terminal"):
            mark_stage_running(run, "market_data")

    def test_invalid_skipped_to_running(self):
        run = _fresh_run()
        mark_stage_skipped(run, "market_data")
        with pytest.raises(ValueError, match="terminal"):
            mark_stage_running(run, "market_data")

    def test_double_start_rejected(self):
        run = _fresh_run()
        mark_stage_running(run, "market_data")
        with pytest.raises(ValueError, match="Invalid stage transition"):
            mark_stage_running(run, "market_data")

    def test_summary_counts_merged(self):
        run = _fresh_run()
        mark_stage_running(run, "scanners")
        mark_stage_completed(run, "scanners", summary_counts={"found": 42})
        assert run["stages"]["scanners"]["summary_counts"]["found"] == 42

    def test_artifact_refs_extended(self):
        run = _fresh_run()
        mark_stage_running(run, "market_data")
        mark_stage_completed(run, "market_data", artifact_refs=["market_data.json"])
        assert "market_data.json" in run["stages"]["market_data"]["artifact_refs"]

    def test_failed_error_appended_to_run(self):
        run = _fresh_run()
        err = build_run_error(code="ERR", message="fail", source="scanners")
        _run_through_stage(run, "scanners", "failed", error=err)
        assert len(run["errors"]) == 1
        assert run["errors"][0]["code"] == "ERR"

    def test_failed_no_error_no_append(self):
        run = _fresh_run()
        mark_stage_running(run, "market_data")
        mark_stage_failed(run, "market_data")
        assert len(run["errors"]) == 0

    def test_unknown_stage_raises_key_error(self):
        run = _fresh_run()
        with pytest.raises(KeyError, match="not found"):
            mark_stage_running(run, "nonexistent_stage")

    def test_running_sets_run_status_to_running(self):
        run = _fresh_run()
        assert run["status"] == "pending"
        mark_stage_running(run, "market_data")
        assert run["status"] == "running"


# =====================================================================
#  Deterministic run-status rollup
# =====================================================================

class TestRunStatusRollup:

    def test_all_pending(self):
        run = _fresh_run()
        assert compute_run_status(run) == "pending"

    def test_any_running(self):
        run = _fresh_run()
        mark_stage_running(run, "market_data")
        assert compute_run_status(run) == "running"

    def test_all_completed(self):
        run = _fresh_run()
        for key in PIPELINE_STAGES:
            _run_through_stage(run, key, "completed")
        assert compute_run_status(run) == "completed"

    def test_all_terminal_with_failure(self):
        run = _fresh_run()
        # Fail first, complete/skip rest
        _run_through_stage(run, "market_data", "failed")
        for key in PIPELINE_STAGES[1:]:
            mark_stage_skipped(run, key)
        assert compute_run_status(run) == "failed"

    def test_failed_with_pending_is_partial_failed(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "failed")
        # Rest still pending → partial_failed
        assert compute_run_status(run) == "partial_failed"

    def test_cancelled_sticky(self):
        run = _fresh_run()
        run["status"] = "cancelled"
        assert compute_run_status(run) == "cancelled"

    def test_completed_plus_skipped_is_completed(self):
        run = _fresh_run()
        for key in PIPELINE_STAGES[:6]:
            _run_through_stage(run, key, "completed")
        for key in PIPELINE_STAGES[6:]:
            mark_stage_skipped(run, key)
        assert compute_run_status(run) == "completed"

    def test_no_stages_is_pending(self):
        run = _fresh_run()
        run["stages"] = {}
        assert compute_run_status(run) == "pending"

    def test_some_completed_some_pending_no_failed(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "completed")
        # Rest pending
        assert compute_run_status(run) == "running"

    def test_multiple_failures_all_terminal(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "failed")
        _run_through_stage(run, "scanners", "failed")
        for key in PIPELINE_STAGES:
            if run["stages"][key]["status"] == "pending":
                mark_stage_skipped(run, key)
        assert compute_run_status(run) == "failed"


# =====================================================================
#  Failed-stage → failed-run behavior
# =====================================================================

class TestFailedRunBehavior:

    def test_single_failed_stage_seals_run(self):
        run = _fresh_run()
        err = build_run_error(code="DATA_ERR", message="bad data", source="market_data")
        _run_through_stage(run, "market_data", "failed", error=err)
        for key in PIPELINE_STAGES[1:]:
            mark_stage_skipped(run, key)
        finalize_run(run)
        assert run["status"] == "failed"
        assert len(run["errors"]) == 1

    def test_multiple_failed_stages(self):
        run = _fresh_run()
        for key in PIPELINE_STAGES[:3]:
            err = build_run_error(code="ERR", message=f"{key} failed", source=key)
            _run_through_stage(run, key, "failed", error=err)
        for key in PIPELINE_STAGES[3:]:
            mark_stage_skipped(run, key)
        finalize_run(run)
        assert run["status"] == "failed"
        assert len(run["errors"]) == 3


# =====================================================================
#  Partial / incomplete run behavior
# =====================================================================

class TestPartialRunBehavior:

    def test_some_completed_some_pending(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "completed")
        _run_through_stage(run, "market_model_analysis", "completed")
        # Others still pending
        assert compute_run_status(run) == "running"

    def test_some_completed_some_failed_some_pending(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "completed")
        _run_through_stage(run, "scanners", "failed")
        # Others pending → partial_failed
        assert compute_run_status(run) == "partial_failed"


# =====================================================================
#  Structured error object shape
# =====================================================================

class TestRunError:

    def test_required_keys(self):
        err = build_run_error(code="TEST", message="test error")
        for key in _REQUIRED_ERROR_KEYS:
            assert key in err, f"missing: {key}"

    def test_retryable_default_false(self):
        err = build_run_error(code="X", message="x")
        assert err["retryable"] is False

    def test_retryable_true(self):
        err = build_run_error(code="X", message="x", retryable=True)
        assert err["retryable"] is True

    def test_detail_default_empty(self):
        err = build_run_error(code="X", message="x")
        assert err["detail"] == {}

    def test_detail_passthrough(self):
        err = build_run_error(code="X", message="x", detail={"timeout": 30})
        assert err["detail"]["timeout"] == 30

    def test_source_default_empty(self):
        err = build_run_error(code="X", message="x")
        assert err["source"] == ""

    def test_source_passthrough(self):
        err = build_run_error(code="X", message="x", source="scanners")
        assert err["source"] == "scanners"

    def test_timestamp_present(self):
        err = build_run_error(code="X", message="x")
        assert isinstance(err["timestamp"], str)
        assert len(err["timestamp"]) > 0

    def test_code_and_message(self):
        err = build_run_error(code="MARKET_DATA_TIMEOUT", message="Request timed out")
        assert err["code"] == "MARKET_DATA_TIMEOUT"
        assert err["message"] == "Request timed out"


# =====================================================================
#  Structured log event shape
# =====================================================================

class TestLogEvent:

    def test_required_keys(self):
        evt = build_log_event(
            run_id="r1", event_type="stage_started", message="starting"
        )
        for key in _REQUIRED_LOG_EVENT_KEYS:
            assert key in evt, f"missing: {key}"

    def test_event_type_value(self):
        evt = build_log_event(
            run_id="r1", event_type="stage_completed", message="done"
        )
        assert evt["event_type"] == "stage_completed"

    def test_stage_key_optional(self):
        evt = build_log_event(
            run_id="r1", event_type="run_started", message="go"
        )
        assert evt["stage_key"] == ""

    def test_stage_key_passthrough(self):
        evt = build_log_event(
            run_id="r1", stage_key="scanners",
            event_type="stage_started", message="go"
        )
        assert evt["stage_key"] == "scanners"

    def test_metadata_default_empty(self):
        evt = build_log_event(run_id="r1", event_type="progress", message="x")
        assert evt["metadata"] == {}

    def test_metadata_passthrough(self):
        evt = build_log_event(
            run_id="r1", event_type="progress", message="x",
            metadata={"pct": 50}
        )
        assert evt["metadata"]["pct"] == 50

    def test_level_default_info(self):
        evt = build_log_event(run_id="r1", event_type="progress", message="x")
        assert evt["level"] == "info"

    def test_level_passthrough(self):
        evt = build_log_event(
            run_id="r1", event_type="stage_failed",
            message="x", level="error"
        )
        assert evt["level"] == "error"

    def test_timestamp_present(self):
        evt = build_log_event(run_id="r1", event_type="progress", message="x")
        assert isinstance(evt["timestamp"], str)

    def test_run_id_passthrough(self):
        evt = build_log_event(run_id="run-abc", event_type="run_started", message="x")
        assert evt["run_id"] == "run-abc"

    def test_unknown_event_type_no_crash(self):
        """Unknown event_type logs warning but doesn't raise."""
        evt = build_log_event(
            run_id="r1", event_type="invented_type", message="x"
        )
        assert evt["event_type"] == "invented_type"

    def test_unknown_level_no_crash(self):
        """Unknown log level logs warning but doesn't raise."""
        evt = build_log_event(
            run_id="r1", event_type="progress",
            message="x", level="trace"
        )
        assert evt["level"] == "trace"


# =====================================================================
#  Validation
# =====================================================================

class TestValidation:

    def test_valid_run_passes(self):
        run = _fresh_run()
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Errors: {errors}"

    def test_non_dict_fails(self):
        ok, errors = validate_pipeline_run("not_a_dict")
        assert not ok

    def test_missing_key(self):
        run = _fresh_run()
        del run["status"]
        ok, errors = validate_pipeline_run(run)
        assert not ok
        assert any("status" in e for e in errors)

    def test_wrong_version(self):
        run = _fresh_run()
        run["pipeline_version"] = "99.0"
        ok, errors = validate_pipeline_run(run)
        assert not ok
        assert any("99.0" in e for e in errors)

    def test_invalid_run_status(self):
        run = _fresh_run()
        run["status"] = "exploded"
        ok, errors = validate_pipeline_run(run)
        assert not ok
        assert any("exploded" in e for e in errors)

    def test_stages_not_dict_fails(self):
        run = _fresh_run()
        run["stages"] = "wrong"
        ok, errors = validate_pipeline_run(run)
        assert not ok

    def test_stage_missing_key_detected(self):
        run = _fresh_run()
        del run["stages"]["market_data"]["status"]
        ok, errors = validate_pipeline_run(run)
        assert not ok
        assert any("market_data" in e and "status" in e for e in errors)

    def test_stage_invalid_status_detected(self):
        run = _fresh_run()
        run["stages"]["market_data"]["status"] = "exploded"
        ok, errors = validate_pipeline_run(run)
        assert not ok
        assert any("market_data" in e and "exploded" in e for e in errors)

    def test_stage_order_not_list_fails(self):
        run = _fresh_run()
        run["stage_order"] = "wrong"
        ok, errors = validate_pipeline_run(run)
        assert not ok

    def test_errors_not_list_fails(self):
        run = _fresh_run()
        run["errors"] = "wrong"
        ok, errors = validate_pipeline_run(run)
        assert not ok

    def test_round_trip_all_cases(self):
        """Every build output must pass validate."""
        # Fresh run
        run = _fresh_run()
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Fresh run failed: {errors}"

        # After running a stage
        mark_stage_running(run, "market_data")
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Running stage failed: {errors}"

        # After completing a stage
        mark_stage_completed(run, "market_data")
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Completed stage failed: {errors}"

        # After failing a stage
        err = build_run_error(code="X", message="x")
        mark_stage_running(run, "scanners")
        mark_stage_failed(run, "scanners", error=err)
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Failed stage failed: {errors}"

        # After skipping a stage
        mark_stage_skipped(run, "events")
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Skipped stage failed: {errors}"

        # After finalize
        for key in PIPELINE_STAGES:
            if run["stages"][key]["status"] == "pending":
                mark_stage_skipped(run, key)
        finalize_run(run)
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Finalized run failed: {errors}"


# =====================================================================
#  Run summary
# =====================================================================

class TestRunSummary:

    def test_summary_keys(self):
        run = _fresh_run()
        s = run_summary(run)
        expected = {
            "run_id", "pipeline_version", "status", "trigger_source",
            "started_at", "ended_at", "duration_ms", "stage_statuses",
            "completed_stages", "failed_stages", "pending_stages",
            "error_count", "module_role",
        }
        assert expected.issubset(s.keys())

    def test_module_role(self):
        run = _fresh_run()
        s = run_summary(run)
        assert s["module_role"] == "contract"

    def test_stage_status_counts(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "completed")
        _run_through_stage(run, "scanners", "failed")
        mark_stage_skipped(run, "events")
        s = run_summary(run)
        assert s["completed_stages"] == 1
        assert s["failed_stages"] == 1
        assert s["pending_stages"] == len(PIPELINE_STAGES) - 3

    def test_error_count(self):
        run = _fresh_run()
        err = build_run_error(code="X", message="x")
        _run_through_stage(run, "market_data", "failed", error=err)
        s = run_summary(run)
        assert s["error_count"] == 1


# =====================================================================
#  Serialization-friendly
# =====================================================================

class TestSerialization:

    def test_json_serializable_fresh(self):
        run = _fresh_run()
        serialized = json.dumps(run)
        assert isinstance(serialized, str)
        roundtrip = json.loads(serialized)
        assert roundtrip["run_id"] == run["run_id"]

    def test_json_serializable_after_lifecycle(self):
        run = _fresh_run()
        err = build_run_error(code="X", message="x", detail={"key": "val"})
        _run_through_stage(run, "market_data", "completed",
                           summary_counts={"rows": 100},
                           artifact_refs=["market.json"])
        _run_through_stage(run, "scanners", "failed", error=err)
        mark_stage_skipped(run, "events", reason="not needed")
        finalize_run(run)
        serialized = json.dumps(run)
        roundtrip = json.loads(serialized)
        ok, errors = validate_pipeline_run(roundtrip)
        assert ok, f"Round-trip validation failed: {errors}"

    def test_error_json_serializable(self):
        err = build_run_error(
            code="TEST", message="test", source="s",
            detail={"nested": {"key": "val"}}, retryable=True,
        )
        serialized = json.dumps(err)
        roundtrip = json.loads(serialized)
        assert roundtrip["code"] == "TEST"

    def test_log_event_json_serializable(self):
        evt = build_log_event(
            run_id="r1", stage_key="scanners",
            event_type="stage_started", message="go",
            metadata={"count": 5},
        )
        serialized = json.dumps(evt)
        roundtrip = json.loads(serialized)
        assert roundtrip["event_type"] == "stage_started"


# =====================================================================
#  Finalize run
# =====================================================================

class TestFinalizeRun:

    def test_finalize_sets_ended_at(self):
        run = _fresh_run()
        for key in PIPELINE_STAGES:
            _run_through_stage(run, key, "completed")
        finalize_run(run)
        assert run["ended_at"] is not None

    def test_finalize_sets_duration_ms(self):
        run = _fresh_run()
        for key in PIPELINE_STAGES:
            _run_through_stage(run, key, "completed")
        finalize_run(run)
        assert run["duration_ms"] is not None
        assert run["duration_ms"] >= 0

    def test_finalize_computes_correct_status(self):
        run = _fresh_run()
        for key in PIPELINE_STAGES:
            _run_through_stage(run, key, "completed")
        finalize_run(run)
        assert run["status"] == "completed"

    def test_finalize_failed_run(self):
        run = _fresh_run()
        _run_through_stage(run, "market_data", "failed")
        for key in PIPELINE_STAGES[1:]:
            mark_stage_skipped(run, key)
        finalize_run(run)
        assert run["status"] == "failed"


# =====================================================================
#  Constants
# =====================================================================

class TestConstants:

    def test_pipeline_stages_tuple(self):
        assert isinstance(PIPELINE_STAGES, tuple)
        assert len(PIPELINE_STAGES) == 12

    def test_stage_labels_match_stages(self):
        for key in PIPELINE_STAGES:
            assert key in STAGE_LABELS

    def test_valid_stage_statuses(self):
        expected = {"pending", "running", "completed", "failed", "skipped"}
        assert VALID_STAGE_STATUSES == expected

    def test_valid_run_statuses(self):
        expected = {"pending", "running", "completed", "failed",
                    "cancelled", "partial_failed"}
        assert VALID_RUN_STATUSES == expected

    def test_valid_event_types_non_empty(self):
        assert len(VALID_EVENT_TYPES) >= 10

    def test_valid_log_levels(self):
        expected = {"debug", "info", "warning", "error"}
        assert VALID_LOG_LEVELS == expected

    def test_compatible_versions(self):
        assert "1.0" in _COMPATIBLE_VERSIONS


# =====================================================================
#  Integration — full lifecycle
# =====================================================================

class TestIntegration:

    def test_full_successful_lifecycle(self):
        """Create → run all stages → finalize → validate."""
        run = create_pipeline_run(
            trigger_source="integration_test",
            run_id="run-integ-001",
            requested_scope={"symbols": ["SPY"]},
            metadata={"test": True},
        )
        assert run["status"] == "pending"
        ok, errors = validate_pipeline_run(run)
        assert ok, f"Initial: {errors}"

        # Run all stages sequentially
        for key in PIPELINE_STAGES:
            mark_stage_running(run, key)
            assert run["status"] == "running"
            mark_stage_completed(run, key, summary_counts={"items": 1})

        finalize_run(run)
        assert run["status"] == "completed"
        assert run["ended_at"] is not None
        assert run["duration_ms"] is not None
        assert run["duration_ms"] >= 0
        assert len(run["errors"]) == 0

        ok, errors = validate_pipeline_run(run)
        assert ok, f"Final: {errors}"

        s = run_summary(run)
        assert s["completed_stages"] == 12
        assert s["failed_stages"] == 0
        assert s["error_count"] == 0

    def test_full_failed_lifecycle(self):
        """Create → run some stages → fail one → skip rest → finalize."""
        run = create_pipeline_run(
            trigger_source="integration_test",
            run_id="run-integ-002",
        )

        _run_through_stage(run, "market_data", "completed")
        _run_through_stage(run, "market_model_analysis", "completed")

        err = build_run_error(
            code="SCANNER_CRASH", message="Scanner raised exception",
            source="scanners", detail={"exception": "ValueError"},
            retryable=True,
        )
        _run_through_stage(run, "scanners", "failed", error=err)

        for key in PIPELINE_STAGES[3:]:
            mark_stage_skipped(run, key, reason="upstream failure")

        finalize_run(run)
        assert run["status"] == "failed"
        assert len(run["errors"]) == 1
        assert run["errors"][0]["retryable"] is True

        ok, errors = validate_pipeline_run(run)
        assert ok, f"Final: {errors}"

        s = run_summary(run)
        assert s["completed_stages"] == 2
        assert s["failed_stages"] == 1
        assert s["error_count"] == 1

    def test_mixed_lifecycle_with_events(self):
        """Create run, build log events, verify event shape."""
        run = create_pipeline_run(run_id="run-integ-003")

        evt1 = build_log_event(
            run_id=run["run_id"],
            event_type="run_started",
            message="Pipeline started",
        )
        assert evt1["run_id"] == "run-integ-003"
        assert evt1["event_type"] == "run_started"

        mark_stage_running(run, "market_data")
        evt2 = build_log_event(
            run_id=run["run_id"],
            stage_key="market_data",
            event_type="stage_started",
            message="Fetching market data",
            metadata={"symbols": ["SPY"]},
        )
        assert evt2["stage_key"] == "market_data"
        assert evt2["metadata"]["symbols"] == ["SPY"]

        mark_stage_completed(run, "market_data")
        evt3 = build_log_event(
            run_id=run["run_id"],
            stage_key="market_data",
            event_type="stage_completed",
            message="Market data fetched",
        )
        assert evt3["event_type"] == "stage_completed"
