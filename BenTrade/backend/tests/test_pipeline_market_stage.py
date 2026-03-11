"""Tests for Pipeline Market-Picture Stage v1.0.

Coverage targets:
─── Engine registry
    - default registry has 6 engines
    - override registry replaces defaults
    - empty registry
    - engine entry shape
─── Engine classification
    - enabled engines are eligible
    - disabled engines are skipped
    - no-factory engines are unavailable
─── Per-engine execution
    - success path with summary extraction
    - failure with exception capture
    - unavailable engine result shape
─── Bounded parallel execution
    - all engines succeed
    - one engine fails, others succeed
    - respects max_workers limit
    - deterministic result keyed by engine_key
─── Per-engine result normalization
    - build_engine_result shape
    - all status values
    - timing fields
    - error attachment
    - artifact_ref attachment
    - eligible_for_model_analysis flag
─── Artifact creation and lineage
    - per-engine artifact written for success
    - no artifact for failed engine
    - stage summary artifact written
    - artifact lineage (run_id, stage_key, engine_key in metadata)
─── Stage summary artifact
    - engines_succeeded list
    - engines_failed list
    - engines_skipped list
    - engines_unavailable list
    - artifact_refs mapping
    - stage_status rollup
    - degraded_reasons
    - engine_summaries
─── Event emission
    - engine_started emitted
    - engine_completed emitted
    - engine_failed emitted
    - events have engine_key in metadata
─── Partial failure semantics
    - all succeed → completed
    - one fails → completed (degraded metadata)
    - all fail → failed
    - required engine fails → failed
    - skipped/unavailable → not automatic failure
─── Handler contract (orchestrator-compatible)
    - returns outcome/summary_counts/artifacts/metadata/error
    - outcome is completed or failed
    - summary_counts has engine counts
    - metadata has engine_results
─── Orchestrator integration
    - market stage handler wired as default
    - stage result flows through orchestrator
─── Edge cases
    - empty engine registry
    - no eligible engines
    - engine returns non-dict
    - raw_results_override mode
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.services.pipeline_market_stage import (
    DEFAULT_MAX_WORKERS,
    ENGINE_CREDENTIAL_MAP,
    ENGINE_STATUSES,
    FAILURE_CATEGORIES,
    _STAGE_KEY,
    _extract_engine_summary,
    _make_engine_entry,
    build_engine_result,
    build_stage_summary,
    check_engine_config_eligibility,
    classify_engine_failure,
    get_engine_registry,
    market_stage_handler,
)
from app.services.pipeline_artifact_store import (
    create_artifact_store,
    get_artifact,
    get_artifact_by_key,
    list_artifacts,
    list_stage_artifacts,
)
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    create_pipeline_run,
)
from app.services.pipeline_orchestrator import (
    create_orchestrator,
    execute_stage,
    get_default_handlers,
    run_pipeline_with_handlers,
)


# ── Helper factories ────────────────────────────────────────────

def _make_mock_engine(
    engine_key: str,
    return_value: dict | None = None,
    raise_exc: Exception | None = None,
    enabled: bool = True,
    required: bool = False,
    delay: float = 0.0,
) -> dict:
    """Build a test engine registry entry with a mock callable."""
    result = return_value or {
        "engine_result": {
            "score": 72,
            "label": "Constructive",
            "confidence_score": 80,
            "signal_quality": "medium",
        },
        "as_of": "2026-03-11T12:00:00Z",
    }

    async def _mock_method(force: bool = False):
        if delay:
            import asyncio
            await asyncio.sleep(delay)
        if raise_exc:
            raise raise_exc
        return result

    class MockService:
        pass

    svc = MockService()
    method_name = f"get_{engine_key}_analysis"
    setattr(svc, method_name, _mock_method)

    def _factory():
        return svc

    return _make_engine_entry(
        engine_key,
        f"Mock {engine_key}",
        enabled=enabled,
        required=required,
        service_factory=_factory,
        run_method=method_name,
    )


def _make_test_registry(
    *,
    count: int = 3,
    fail_indices: list[int] | None = None,
    disabled_indices: list[int] | None = None,
    unavailable_indices: list[int] | None = None,
    required_indices: list[int] | None = None,
) -> list[dict]:
    """Build a test engine registry with configurable behavior."""
    fail_set = set(fail_indices or [])
    disabled_set = set(disabled_indices or [])
    unavail_set = set(unavailable_indices or [])
    required_set = set(required_indices or [])

    entries = []
    for i in range(count):
        key = f"test_engine_{i}"
        exc = RuntimeError(f"Engine {key} boom") if i in fail_set else None
        enabled = i not in disabled_set

        if i in unavail_set:
            entry = _make_engine_entry(
                key, f"Test Engine {i}",
                enabled=enabled,
                required=i in required_set,
                service_factory=None,
                run_method="",
            )
        else:
            entry = _make_mock_engine(
                key,
                raise_exc=exc,
                enabled=enabled,
                required=i in required_set,
            )
        entries.append(entry)

    return entries


def _make_run_and_store(run_id: str = "run-test-001"):
    """Create a pipeline run and artifact store pair."""
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    return run, store


# =====================================================================
#  Engine Registry
# =====================================================================

class TestEngineRegistry:

    def test_default_registry_has_six_engines(self):
        reg = get_engine_registry()
        assert len(reg) == 6

    def test_default_registry_engine_keys(self):
        reg = get_engine_registry()
        keys = [e["engine_key"] for e in reg]
        expected = [
            "breadth_participation",
            "volatility_options",
            "liquidity_financial_conditions",
            "cross_asset_macro",
            "flows_positioning",
            "news_sentiment",
        ]
        assert keys == expected

    def test_override_registry_replaces_defaults(self):
        custom = [_make_engine_entry("custom_1", "Custom")]
        reg = get_engine_registry(override_registry=custom)
        assert len(reg) == 1
        assert reg[0]["engine_key"] == "custom_1"

    def test_engine_entry_shape(self):
        entry = _make_engine_entry(
            "test_eng", "Test Engine",
            enabled=True, required=False,
        )
        assert entry["engine_key"] == "test_eng"
        assert entry["display_name"] == "Test Engine"
        assert entry["enabled"] is True
        assert entry["required"] is False
        assert entry["service_factory"] is None
        assert entry["run_method"] == ""


# =====================================================================
#  Engine Result Builder
# =====================================================================

class TestBuildEngineResult:

    def test_success_result_shape(self):
        r = build_engine_result(
            engine_key="breadth_participation",
            status="success",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:00:01Z",
            elapsed_ms=1000,
            summary={"score": 72},
            eligible_for_model_analysis=True,
        )
        assert r["engine_key"] == "breadth_participation"
        assert r["status"] == "success"
        assert r["elapsed_ms"] == 1000
        assert r["summary"]["score"] == 72
        assert r["eligible_for_model_analysis"] is True
        assert r["error"] is None
        assert r["artifact_ref"] is None

    def test_failed_result_shape(self):
        err = {"code": "TEST", "message": "boom"}
        r = build_engine_result(
            engine_key="volatility_options",
            status="failed",
            error=err,
        )
        assert r["status"] == "failed"
        assert r["error"] == err
        assert r["eligible_for_model_analysis"] is False

    def test_all_statuses_accepted(self):
        for status in ENGINE_STATUSES:
            r = build_engine_result(engine_key="x", status=status)
            assert r["status"] == status


# =====================================================================
#  Engine Summary Extraction
# =====================================================================

class TestExtractEngineSummary:

    def test_extracts_from_engine_result(self):
        raw = {
            "engine_result": {
                "score": 65,
                "label": "Mixed",
                "confidence_score": 75,
                "signal_quality": "medium",
            }
        }
        s = _extract_engine_summary("test", raw)
        assert s["score"] == 65
        assert s["label"] == "Mixed"
        assert s["confidence"] == 75
        assert s["signal_quality"] == "medium"

    def test_handles_non_dict(self):
        s = _extract_engine_summary("test", "not a dict")
        assert "raw_type" in s

    def test_handles_missing_engine_result(self):
        s = _extract_engine_summary("test", {})
        assert s["score"] is None


# =====================================================================
#  Stage Summary Builder
# =====================================================================

class TestBuildStageSummary:

    def test_all_succeed(self):
        results = {
            "eng1": build_engine_result(engine_key="eng1", status="success",
                                        summary={"score": 80},
                                        artifact_ref="art-001"),
            "eng2": build_engine_result(engine_key="eng2", status="success",
                                        summary={"score": 60},
                                        artifact_ref="art-002"),
        }
        s = build_stage_summary(results, [], [], elapsed_ms=500)
        assert s["stage_status"] == "success"
        assert s["success_count"] == 2
        assert s["fail_count"] == 0
        assert s["engines_succeeded"] == ["eng1", "eng2"]
        assert s["engines_failed"] == []
        assert s["artifact_refs"]["eng1"] == "art-001"

    def test_partial_failure_is_degraded(self):
        results = {
            "eng1": build_engine_result(engine_key="eng1", status="success"),
            "eng2": build_engine_result(engine_key="eng2", status="failed"),
        }
        s = build_stage_summary(results, [], [])
        assert s["stage_status"] == "degraded"
        assert s["success_count"] == 1
        assert s["fail_count"] == 1
        assert len(s["degraded_reasons"]) > 0

    def test_all_fail(self):
        results = {
            "eng1": build_engine_result(engine_key="eng1", status="failed"),
        }
        s = build_stage_summary(results, [], [])
        assert s["stage_status"] == "failed"

    def test_no_engines(self):
        s = build_stage_summary({}, [], [])
        assert s["stage_status"] == "no_engines"

    def test_skipped_engines_listed(self):
        results = {
            "eng1": build_engine_result(engine_key="eng1", status="success"),
        }
        s = build_stage_summary(results, ["eng_disabled"], ["eng_no_factory"])
        assert "eng_disabled" in s["engines_skipped"]
        assert "eng_no_factory" in s["engines_unavailable"]
        assert s["skip_count"] == 1
        assert s["unavailable_count"] == 1

    def test_engine_summaries_populated(self):
        results = {
            "eng1": build_engine_result(
                engine_key="eng1", status="success",
                summary={"score": 70, "label": "Good"},
                artifact_ref="art-x", elapsed_ms=100,
                eligible_for_model_analysis=True,
            ),
        }
        s = build_stage_summary(results, [], [])
        es = s["engine_summaries"]["eng1"]
        assert es["status"] == "success"
        assert es["score"] == 70
        assert es["artifact_ref"] == "art-x"
        assert es["eligible_for_model_analysis"] is True


# =====================================================================
#  Market Stage Handler — All Engines Succeed
# =====================================================================

class TestMarketStageAllSucceed:

    def test_outcome_completed(self):
        registry = _make_test_registry(count=3)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
            max_workers=2,
        )
        assert result["outcome"] == "completed"
        assert result["error"] is None

    def test_summary_counts(self):
        registry = _make_test_registry(count=3)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        sc = result["summary_counts"]
        assert sc["engines_attempted"] == 3
        assert sc["engines_succeeded"] == 3
        assert sc["engines_failed"] == 0

    def test_artifacts_written(self):
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        # Should have 2 engine artifacts + 1 summary
        arts = list_stage_artifacts(store, _STAGE_KEY)
        assert len(arts) == 3

    def test_summary_artifact_contents(self):
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        summary_art = get_artifact_by_key(store, _STAGE_KEY, "market_stage_summary")
        assert summary_art is not None
        data = summary_art["data"]
        assert data["stage_status"] == "success"
        assert data["success_count"] == 2

    def test_engine_artifact_lineage(self):
        registry = _make_test_registry(count=1)
        run, store = _make_run_and_store("run-lineage-001")
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        art = get_artifact_by_key(store, _STAGE_KEY, "engine_test_engine_0")
        assert art is not None
        assert art["run_id"] == "run-lineage-001"
        assert art["stage_key"] == _STAGE_KEY
        assert art["metadata"]["engine_key"] == "test_engine_0"

    def test_metadata_contains_engine_results(self):
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        er = result["metadata"]["engine_results"]
        assert "test_engine_0" in er
        assert "test_engine_1" in er
        assert er["test_engine_0"]["status"] == "success"


# =====================================================================
#  Partial Failure — One Engine Fails
# =====================================================================

class TestPartialFailure:

    def test_one_fail_still_completed(self):
        registry = _make_test_registry(count=3, fail_indices=[1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["outcome"] == "completed"
        assert result["error"] is None

    def test_degraded_status_in_metadata(self):
        registry = _make_test_registry(count=3, fail_indices=[1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["metadata"]["stage_status"] == "degraded"

    def test_summary_counts_reflect_failure(self):
        registry = _make_test_registry(count=3, fail_indices=[1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        sc = result["summary_counts"]
        assert sc["engines_succeeded"] == 2
        assert sc["engines_failed"] == 1

    def test_failed_engine_has_no_artifact(self):
        registry = _make_test_registry(count=2, fail_indices=[1])
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        # Engine 0 succeeds → has artifact
        art0 = get_artifact_by_key(store, _STAGE_KEY, "engine_test_engine_0")
        assert art0 is not None
        # Engine 1 fails → no artifact
        art1 = get_artifact_by_key(store, _STAGE_KEY, "engine_test_engine_1")
        assert art1 is None


# =====================================================================
#  All Engines Fail → Stage Failure
# =====================================================================

class TestAllEngineFail:

    def test_all_fail_outcome_failed(self):
        registry = _make_test_registry(count=2, fail_indices=[0, 1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["outcome"] == "failed"
        assert result["error"] is not None
        assert result["error"]["code"] == "ALL_ENGINES_FAILED"

    def test_all_fail_summary_artifact_still_written(self):
        registry = _make_test_registry(count=2, fail_indices=[0, 1])
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        summary = get_artifact_by_key(store, _STAGE_KEY, "market_stage_summary")
        assert summary is not None
        assert summary["data"]["stage_status"] == "failed"


# =====================================================================
#  Required Engine Failure
# =====================================================================

class TestRequiredEngineFailure:

    def test_required_engine_fail_forces_stage_failure(self):
        registry = _make_test_registry(
            count=3, fail_indices=[0], required_indices=[0],
        )
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "REQUIRED_ENGINE_FAILED"

    def test_required_engine_succeeds_stage_ok(self):
        registry = _make_test_registry(
            count=3, required_indices=[0],
        )
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["outcome"] == "completed"


# =====================================================================
#  Skipped / Unavailable Engines
# =====================================================================

class TestSkippedUnavailable:

    def test_disabled_engine_skipped(self):
        registry = _make_test_registry(count=3, disabled_indices=[1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["summary_counts"]["engines_skipped"] == 1
        assert result["summary_counts"]["engines_attempted"] == 2

    def test_unavailable_engine_tracked(self):
        registry = _make_test_registry(count=3, unavailable_indices=[2])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["summary_counts"]["engines_unavailable"] == 1

    def test_skipped_does_not_cause_failure(self):
        registry = _make_test_registry(count=3, disabled_indices=[0, 1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        # One engine eligible and succeeds → completed
        assert result["outcome"] == "completed"


# =====================================================================
#  Empty / No Eligible Engines
# =====================================================================

class TestNoEngines:

    def test_empty_registry_fails(self):
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=[],
        )
        assert result["outcome"] == "failed"
        assert result["error"]["code"] == "NO_ELIGIBLE_ENGINES"

    def test_all_disabled_fails(self):
        registry = _make_test_registry(count=2, disabled_indices=[0, 1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["outcome"] == "failed"

    def test_no_engines_summary_artifact_still_written(self):
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=[],
        )
        summary = get_artifact_by_key(store, _STAGE_KEY, "market_stage_summary")
        assert summary is not None
        assert summary["data"]["stage_status"] == "no_engines"


# =====================================================================
#  Bounded Parallel Execution
# =====================================================================

class TestBoundedParallelExecution:

    def test_respects_max_workers(self):
        """All engines complete even with limited workers."""
        registry = _make_test_registry(count=5)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
            max_workers=2,
        )
        assert result["summary_counts"]["engines_succeeded"] == 5

    def test_deterministic_keys(self):
        """Results keyed by engine_key regardless of execution order."""
        registry = _make_test_registry(count=4)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        er = result["metadata"]["engine_results"]
        for i in range(4):
            assert f"test_engine_{i}" in er

    def test_exception_in_one_does_not_lose_others(self):
        """One engine exception does not prevent others from completing."""
        registry = _make_test_registry(count=3, fail_indices=[1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        er = result["metadata"]["engine_results"]
        assert er["test_engine_0"]["status"] == "success"
        assert er["test_engine_1"]["status"] == "failed"
        assert er["test_engine_2"]["status"] == "success"


# =====================================================================
#  Event Emission
# =====================================================================

class TestEventEmission:

    def test_engine_events_emitted(self):
        events = []
        def _cb(event):
            events.append(event)

        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
            event_callback=_cb,
        )
        event_types = [e["event_type"] for e in events]
        assert "engine_started" in event_types
        assert "engine_completed" in event_types

    def test_engine_failure_event(self):
        events = []
        def _cb(event):
            events.append(event)

        registry = _make_test_registry(count=2, fail_indices=[0])
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
            event_callback=_cb,
        )
        event_types = [e["event_type"] for e in events]
        assert "engine_failed" in event_types

    def test_events_have_engine_key(self):
        events = []
        def _cb(event):
            events.append(event)

        registry = _make_test_registry(count=1)
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
            event_callback=_cb,
        )
        for e in events:
            assert "engine_key" in e.get("metadata", {})

    def test_event_callback_exception_does_not_crash(self):
        def _bad_cb(event):
            raise ValueError("callback boom")

        registry = _make_test_registry(count=1)
        run, store = _make_run_and_store()
        # Should not raise
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
            event_callback=_bad_cb,
        )
        assert result["outcome"] == "completed"


# =====================================================================
#  Raw Results Override (Test/Replay Mode)
# =====================================================================

class TestRawResultsOverride:

    def test_uses_override_results(self):
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        raw = {
            "test_engine_0": {
                "engine_result": {"score": 90, "label": "Strong"},
            },
            "test_engine_1": {
                "engine_result": {"score": 50, "label": "Mixed"},
            },
        }
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
            engine_raw_results=raw,
        )
        assert result["outcome"] == "completed"
        er = result["metadata"]["engine_results"]
        assert er["test_engine_0"]["summary"]["score"] == 90

    def test_override_missing_engine_treated_as_skipped(self):
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        raw = {
            "test_engine_0": {
                "engine_result": {"score": 90},
            },
            # test_engine_1 missing from override
        }
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
            engine_raw_results=raw,
        )
        er = result["metadata"]["engine_results"]
        assert er["test_engine_1"]["status"] == "skipped"


# =====================================================================
#  Handler Contract Shape
# =====================================================================

class TestHandlerContract:

    def test_result_has_required_keys(self):
        registry = _make_test_registry(count=1)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert "outcome" in result
        assert "summary_counts" in result
        assert "artifacts" in result
        assert "metadata" in result
        assert "error" in result

    def test_outcome_is_valid(self):
        registry = _make_test_registry(count=1)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert result["outcome"] in ("completed", "failed", "skipped")

    def test_artifacts_is_list(self):
        registry = _make_test_registry(count=1)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        assert isinstance(result["artifacts"], list)


# =====================================================================
#  Orchestrator Integration
# =====================================================================

class TestOrchestratorIntegration:

    def test_market_handler_wired_by_default(self):
        handlers = get_default_handlers()
        assert handlers["market_data"] is market_stage_handler

    def test_execute_stage_with_market_handler(self):
        """Execute market stage via the orchestrator wrapper with mock engines."""
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        result = execute_stage(
            run, store, _STAGE_KEY,
            handler=market_stage_handler,
            handler_kwargs={
                "engine_registry": registry,
                "max_workers": 2,
            },
        )
        assert result["outcome"] == "completed"
        assert result["handler_invoked"] is True
        assert run["stages"][_STAGE_KEY]["status"] == "completed"

    def test_failed_market_stage_through_orchestrator(self):
        registry = _make_test_registry(count=2, fail_indices=[0, 1])
        run, store = _make_run_and_store()
        result = execute_stage(
            run, store, _STAGE_KEY,
            handler=market_stage_handler,
            handler_kwargs={
                "engine_registry": registry,
            },
        )
        assert result["outcome"] == "failed"
        assert run["stages"][_STAGE_KEY]["status"] == "failed"

    def test_full_pipeline_with_mock_market_handler(self):
        """Run full pipeline with mock market engines; other stages use stubs."""
        registry = _make_test_registry(count=2)

        def _market_handler(run, artifact_store, stage_key, **kwargs):
            return market_stage_handler(
                run, artifact_store, stage_key,
                engine_registry=registry,
                **kwargs,
            )

        result = run_pipeline_with_handlers(
            {
                "market_data": _market_handler,
                "market_model_analysis": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "scanners": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "candidate_selection": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "shared_context": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "candidate_enrichment": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "events": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "policy": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "orchestration": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "prompt_payload": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "final_model_decision": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
                "final_response_normalization": lambda run, store, sk, **kw: {
                    "outcome": "completed", "summary_counts": {},
                    "artifacts": [], "metadata": {}, "error": None,
                },
            },
            run_id="run-full-001",
        )
        # Market data should complete
        market_sr = next(
            sr for sr in result["stage_results"]
            if sr["stage_key"] == "market_data"
        )
        assert market_sr["outcome"] == "completed"
        # All stages should ultimately complete (stubs for the rest)
        assert result["run"]["status"] == "completed"


# =====================================================================
#  Deterministic / Stable Output Shape
# =====================================================================

class TestOutputStability:

    def test_engine_result_keys_stable(self):
        r = build_engine_result(engine_key="x", status="success")
        expected_keys = {
            "engine_key", "status", "started_at", "completed_at",
            "elapsed_ms", "summary", "error", "artifact_ref",
            "eligible_for_model_analysis",
        }
        assert set(r.keys()) == expected_keys

    def test_stage_summary_keys_stable(self):
        s = build_stage_summary({}, [], [])
        expected_keys = {
            "stage_key", "stage_status", "total_attempted",
            "engines_succeeded", "engines_failed", "engines_degraded",
            "engines_skipped", "engines_unavailable",
            "success_count", "fail_count", "skip_count", "unavailable_count",
            "artifact_refs", "degraded_reasons", "engine_summaries",
            "elapsed_ms", "generated_at",
        }
        assert set(s.keys()) == expected_keys

    def test_handler_result_keys_stable(self):
        registry = _make_test_registry(count=1)
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        expected_keys = {"outcome", "summary_counts", "artifacts", "metadata", "error"}
        assert set(result.keys()) == expected_keys


# =====================================================================
#  Step 5 Forward Compatibility
# =====================================================================

class TestStep5Compatibility:
    """Verify that Step 4 outputs leave behind the data Step 5 needs."""

    def test_eligible_for_model_analysis_flag(self):
        registry = _make_test_registry(count=3, fail_indices=[1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        er = result["metadata"]["engine_results"]
        assert er["test_engine_0"]["eligible_for_model_analysis"] is True
        assert er["test_engine_1"]["eligible_for_model_analysis"] is False

    def test_summary_artifact_has_eligible_engines(self):
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        summary = get_artifact_by_key(store, _STAGE_KEY, "market_stage_summary")
        data = summary["data"]
        for key, es in data["engine_summaries"].items():
            assert "eligible_for_model_analysis" in es

    def test_engine_artifacts_retrievable_by_key(self):
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        for i in range(2):
            art = get_artifact_by_key(
                store, _STAGE_KEY, f"engine_test_engine_{i}",
            )
            assert art is not None
            assert art["artifact_type"] == "market_engine_output"


# =====================================================================
#  Config Eligibility
# =====================================================================

class TestConfigEligibility:
    """Verify preflight API-key eligibility checks."""

    def test_all_keys_present(self):
        """When all keys set, all engines are eligible."""
        settings = MagicMock()
        settings.TRADIER_TOKEN = "tok"
        settings.FRED_KEY = "fk"
        settings.FINNHUB_KEY = "fhk"
        settings.POLYGON_API_KEY = "pk"
        result = check_engine_config_eligibility(settings)
        for engine_key, info in result.items():
            assert info["eligible"] is True, f"{engine_key} not eligible"
            assert info["missing_required"] == []

    def test_no_keys_present(self):
        """When no keys set, engines with required keys are ineligible."""
        settings = MagicMock()
        settings.TRADIER_TOKEN = ""
        settings.FRED_KEY = ""
        settings.FINNHUB_KEY = ""
        settings.POLYGON_API_KEY = ""
        result = check_engine_config_eligibility(settings)
        # breadth and volatility need TRADIER_TOKEN
        assert result["breadth_participation"]["eligible"] is False
        assert "TRADIER_TOKEN" in result["breadth_participation"]["missing_required"]
        assert result["volatility_options"]["eligible"] is False
        # liquidity and cross_asset need FRED_KEY
        assert result["liquidity_financial_conditions"]["eligible"] is False
        assert result["cross_asset_macro"]["eligible"] is False
        # flows and news have no required keys
        assert result["flows_positioning"]["eligible"] is True
        assert result["news_sentiment"]["eligible"] is True

    def test_missing_optional_keys_reported(self):
        """Missing optional keys are reported but don't block eligibility."""
        settings = MagicMock()
        settings.TRADIER_TOKEN = "tok"
        settings.FRED_KEY = ""
        settings.FINNHUB_KEY = ""
        settings.POLYGON_API_KEY = ""
        result = check_engine_config_eligibility(settings)
        # volatility eligible (TRADIER present), but FRED/FINNHUB optional missing
        assert result["volatility_options"]["eligible"] is True
        assert "FRED_KEY" in result["volatility_options"]["missing_optional"]

    def test_covers_all_engines(self):
        """All 6 canonical engines are covered in the credential map."""
        expected = {
            "breadth_participation", "volatility_options",
            "liquidity_financial_conditions", "cross_asset_macro",
            "flows_positioning", "news_sentiment",
        }
        assert set(ENGINE_CREDENTIAL_MAP.keys()) == expected

    def test_default_settings_returns_valid_shape(self):
        """check_engine_config_eligibility with None settings still works."""
        # Uses real Settings() which reads env — just verify no crash
        result = check_engine_config_eligibility()
        assert isinstance(result, dict)
        assert len(result) == 6
        for info in result.values():
            assert "eligible" in info
            assert "missing_required" in info
            assert "missing_optional" in info


# =====================================================================
#  Failure Classification
# =====================================================================

class TestFailureClassification:
    """Verify engine failure classification logic."""

    def test_categories_are_valid(self):
        """All classification results are from the declared set."""
        exceptions = [
            RuntimeError("401 Unauthorized"),
            RuntimeError("403 Forbidden"),
            RuntimeError("429 Too Many Requests"),
            TimeoutError("Request timed out"),
            ConnectionError("Connection refused"),
            TypeError("__init__ missing argument"),
            AttributeError("Service has no method 'foo'"),
            RuntimeError("Something totally unexpected"),
        ]
        for exc in exceptions:
            cat = classify_engine_failure(exc)
            assert cat in FAILURE_CATEGORIES, f"'{cat}' not in FAILURE_CATEGORIES"

    def test_authentication_error(self):
        assert classify_engine_failure(RuntimeError("401 Unauthorized")) == "authentication_error"
        assert classify_engine_failure(RuntimeError("403 Forbidden")) == "authentication_error"

    def test_rate_limited(self):
        assert classify_engine_failure(RuntimeError("429 Too Many Requests")) == "rate_limited"
        assert classify_engine_failure(RuntimeError("rate limit exceeded")) == "rate_limited"

    def test_timeout(self):
        assert classify_engine_failure(TimeoutError("timed out")) == "timeout"
        assert classify_engine_failure(RuntimeError("read timeout")) == "timeout"

    def test_network_error(self):
        assert classify_engine_failure(ConnectionError("connection refused")) == "network_error"
        assert classify_engine_failure(RuntimeError("dns resolution failed")) == "network_error"

    def test_missing_configuration(self):
        assert classify_engine_failure(ValueError("api key not configured")) == "missing_configuration"

    def test_construction_error(self):
        assert classify_engine_failure(
            AttributeError("Service has no method 'get_foo'")
        ) == "construction_error"
        assert classify_engine_failure(
            TypeError("__init__ missing required argument")
        ) == "construction_error"

    def test_provider_error(self):
        assert classify_engine_failure(RuntimeError("500 Internal Server Error")) == "provider_error"
        assert classify_engine_failure(RuntimeError("502 Bad Gateway")) == "provider_error"

    def test_unexpected_error(self):
        assert classify_engine_failure(RuntimeError("something weird")) == "unexpected_error"


# =====================================================================
#  Stage summary with config eligibility
# =====================================================================

class TestStageSummaryWithConfigEligibility:
    """Config eligibility included in stage summary when provided."""

    def test_config_eligibility_in_summary(self):
        results = {
            "eng1": build_engine_result(engine_key="eng1", status="success"),
        }
        elig = {"eng1": {"eligible": True, "missing_required": [], "missing_optional": []}}
        s = build_stage_summary(results, [], [], config_eligibility=elig)
        assert s["config_eligibility"] == elig

    def test_no_config_eligibility_omitted(self):
        results = {
            "eng1": build_engine_result(engine_key="eng1", status="success"),
        }
        s = build_stage_summary(results, [], [])
        assert "config_eligibility" not in s

    def test_failure_category_in_engine_summaries(self):
        from app.services.pipeline_run_contract import build_run_error
        results = {
            "eng1": build_engine_result(
                engine_key="eng1", status="failed",
                error=build_run_error(
                    code="ENGINE_EXCEPTION",
                    message="timeout",
                    source="eng1",
                    detail={"failure_category": "timeout"},
                ),
            ),
        }
        s = build_stage_summary(results, [], [])
        assert s["engine_summaries"]["eng1"]["failure_category"] == "timeout"

    def test_success_has_no_failure_category(self):
        results = {
            "eng1": build_engine_result(engine_key="eng1", status="success"),
        }
        s = build_stage_summary(results, [], [])
        assert s["engine_summaries"]["eng1"]["failure_category"] is None


# =====================================================================
#  Handler includes config eligibility
# =====================================================================

class TestHandlerConfigEligibility:
    """Handler injects config eligibility into the stage summary."""

    def test_summary_artifact_has_config_eligibility(self):
        registry = _make_test_registry(count=2)
        run, store = _make_run_and_store()
        market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        summary = get_artifact_by_key(store, _STAGE_KEY, "market_stage_summary")
        data = summary["data"]
        assert "config_eligibility" in data
        assert isinstance(data["config_eligibility"], dict)
        # All 6 canonical engines should be checked
        assert len(data["config_eligibility"]) == 6

    def test_failure_category_flows_through_handler(self):
        """When an engine fails, the failure_category appears in the summary."""
        registry = _make_test_registry(count=2, fail_indices=[1])
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=registry,
        )
        er = result["metadata"]["engine_results"]
        failed_rec = er["test_engine_1"]
        assert failed_rec["status"] == "failed"
        err = failed_rec["error"]
        assert "failure_category" in err.get("detail", {})


# =====================================================================
#  Client lifecycle (factory returns tuple)
# =====================================================================

class TestFactoryTupleContract:
    """Verify that factories returning (service, http_client) work."""

    def test_bare_service_factory_still_works(self):
        """Old-style factory returning bare service is backward compatible."""
        class Svc:
            async def get_test_bare_analysis(self, force=False):
                return {"engine_result": {"score": 50}}

        # Factory returns bare service (no tuple)
        entry = _make_engine_entry(
            "test_bare", "Test Bare",
            service_factory=lambda: Svc(),
            run_method="get_test_bare_analysis",
        )
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=[entry],
        )
        assert result["outcome"] == "completed"

    def test_tuple_factory_works(self):
        """Factory returning (service, http_client) works and client is closeable."""
        class Svc:
            async def get_test_tuple_analysis(self, force=False):
                return {"engine_result": {"score": 77}}

        mock_client = MagicMock()
        # aclose is async
        async def _aclose():
            pass
        mock_client.aclose = _aclose

        entry = _make_engine_entry(
            "test_tuple", "Test Tuple",
            service_factory=lambda: (Svc(), mock_client),
            run_method="get_test_tuple_analysis",
        )
        run, store = _make_run_and_store()
        result = market_stage_handler(
            run, store, _STAGE_KEY,
            engine_registry=[entry],
        )
        assert result["outcome"] == "completed"
