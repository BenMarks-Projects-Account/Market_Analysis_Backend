"""Tests for Pipeline Context Assembly Stage (Step 8).

Coverage targets:
─── Upstream retrieval
    - market module retrieval (full, partial, missing)
    - model module retrieval (full, partial, missing)
    - selection module retrieval (full, missing, degraded)
─── Per-module assembly records
    - record shape and required keys
    - assembly_status vocabulary (full / degraded / failed)
    - elapsed_ms captured
─── Overall assembly status
    - all modules full → "full"
    - any module degraded → "degraded"
    - any module failed → "failed"
─── Shared context structure
    - context_modules dict shape
    - module_records list
    - degraded_reasons aggregated
    - assembled_at timestamp present
─── Artifact creation
    - shared_context artifact written
    - context_assembly_summary artifact written
    - artifact retrieval by key
─── Stage summary
    - contains expected fields
    - counts correct
    - artifact refs populated
─── Event emission
    - context_assembly_started event
    - context_assembly_completed event
    - context_assembly_failed event
─── Handler contract
    - returns expected dict shape
    - outcome field values (completed / failed)
    - summary_counts fields
    - metadata fields
─── Degraded assembly
    - partial upstream data → completed + degraded status
    - missing market summary → still assembles others
    - missing model summary → still assembles others
─── Failed assembly
    - all modules missing → outcome failed
─── Orchestrator integration
    - default handler wired
    - runs through pipeline with stubs
─── Forward compatibility
    - retrieval seam for downstream enrichment
"""

import pytest

from app.services.pipeline_context_assembly_stage import (
    _STAGE_KEY,
    _CONTEXT_MODULES,
    _MODULE_MARKET,
    _MODULE_MODEL,
    _MODULE_SELECTION,
    ASSEMBLY_STATUS_FULL,
    ASSEMBLY_STATUS_DEGRADED,
    ASSEMBLY_STATUS_FAILED,
    _retrieve_market_module,
    _retrieve_model_module,
    _retrieve_selection_module,
    _build_module_record,
    _compute_overall_assembly_status,
    assemble_shared_context,
    context_assembly_handler,
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
    run_pipeline_with_handlers,
)


# =====================================================================
#  Helper factories
# =====================================================================

def _make_run_and_store(run_id="test-ctx-001"):
    """Create a fresh run+store with upstream stages completed."""
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    # Complete prerequisite stages
    for stage in (
        "market_data", "market_model_analysis",
        "stock_scanners", "options_scanners", "candidate_selection",
    ):
        mark_stage_running(run, stage)
        mark_stage_completed(run, stage)
    return run, store


def _write_market_artifacts(
    store, run_id,
    *,
    engine_keys=("breadth", "volatility"),
    stage_status="success",
    degraded_reasons=None,
    engine_data=None,
):
    """Write Step 4 market-data artifacts into the store."""
    if degraded_reasons is None:
        degraded_reasons = []

    engine_summaries = {}
    for ek in engine_keys:
        data = (engine_data or {}).get(ek, {"engine_key": ek, "score": 72.0})
        art = build_artifact_record(
            run_id=run_id,
            stage_key="market_data",
            artifact_key=f"engine_{ek}",
            artifact_type="market_engine_output",
            data=data,
            summary={"engine_key": ek},
        )
        put_artifact(store, art, overwrite=True)
        engine_summaries[ek] = {"engine_key": ek, "status": "success"}

    summary_data = {
        "stage_key": "market_data",
        "stage_status": stage_status,
        "total_attempted": len(engine_keys),
        "engines_succeeded": list(engine_keys),
        "engines_failed": [],
        "engines_skipped": [],
        "engines_unavailable": [],
        "success_count": len(engine_keys),
        "fail_count": 0,
        "skip_count": 0,
        "unavailable_count": 0,
        "artifact_refs": {ek: f"art-{ek}" for ek in engine_keys},
        "engine_summaries": engine_summaries,
        "degraded_reasons": degraded_reasons,
        "elapsed_ms": 100,
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="market_data",
        artifact_key="market_stage_summary",
        artifact_type="market_stage_summary",
        data=summary_data,
        summary={"stage_status": stage_status},
    )
    put_artifact(store, art, overwrite=True)
    return summary_data


def _write_model_artifacts(
    store, run_id,
    *,
    engine_keys=("breadth", "volatility"),
    stage_status="success",
    degraded_reasons=None,
    model_data=None,
):
    """Write Step 5 model-analysis artifacts into the store."""
    if degraded_reasons is None:
        degraded_reasons = []

    engine_summaries = {}
    for ek in engine_keys:
        data = (model_data or {}).get(
            ek, {"engine_key": ek, "analysis": {"bias": "neutral"}}
        )
        art = build_artifact_record(
            run_id=run_id,
            stage_key="market_model_analysis",
            artifact_key=f"model_{ek}",
            artifact_type="market_model_output",
            data=data,
            summary={"engine_key": ek},
        )
        put_artifact(store, art, overwrite=True)
        engine_summaries[ek] = {"engine_key": ek, "status": "analyzed"}

    summary_data = {
        "stage_key": "market_model_analysis",
        "stage_status": stage_status,
        "total_considered": len(engine_keys),
        "total_attempted": len(engine_keys),
        "engines_analyzed": list(engine_keys),
        "engines_failed": [],
        "engines_skipped": {},
        "analyzed_count": len(engine_keys),
        "failed_count": 0,
        "skipped_count": 0,
        "artifact_refs": {ek: f"model-art-{ek}" for ek in engine_keys},
        "degraded_reasons": degraded_reasons,
        "engine_summaries": engine_summaries,
        "elapsed_ms": 200,
        "generated_at": "2025-01-01T00:00:00+00:00",
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="market_model_analysis",
        artifact_key="model_stage_summary",
        artifact_type="market_model_output",
        data=summary_data,
        summary={"stage_status": stage_status},
    )
    put_artifact(store, art, overwrite=True)
    return summary_data


def _write_selection_artifacts(
    store, run_id,
    *,
    candidates=None,
    stage_status="success",
    degraded_reasons=None,
):
    """Write Step 7 candidate-selection artifacts into the store."""
    if degraded_reasons is None:
        degraded_reasons = []
    if candidates is None:
        candidates = [
            {
                "candidate_id": "cand_001",
                "scanner_key": "put_credit_spread_scanner",
                "symbol": "SPY",
                "strategy_type": "put_credit_spread",
                "scanner_family": "options",
                "setup_quality": 75.0,
                "confidence": 0.8,
                "direction": "long",
                "rank_score": 0.85,
                "rank_position": 1,
                "selection_stage_key": "candidate_selection",
                "downstream_selected": True,
            },
        ]

    # Write selected_candidates artifact
    art = build_artifact_record(
        run_id=run_id,
        stage_key="candidate_selection",
        artifact_key="selected_candidates",
        artifact_type="selected_candidate",
        data=candidates,
        summary={
            "total_selected": len(candidates),
            "candidate_ids": [c.get("candidate_id") for c in candidates],
        },
    )
    put_artifact(store, art, overwrite=True)

    # Write selection summary
    summary_data = {
        "stage_key": "candidate_selection",
        "stage_status": stage_status,
        "total_loaded": len(candidates) + 2,
        "total_eligible": len(candidates),
        "total_excluded_pre_ranking": 1,
        "total_duplicates_excluded": 1,
        "total_selected": len(candidates),
        "total_cut_by_rank": 0,
        "selection_cap": 10,
        "selected_candidate_ids": [c.get("candidate_id") for c in candidates],
        "degraded_reasons": degraded_reasons,
        "elapsed_ms": 50,
        "generated_at": "2025-01-01T00:00:00+00:00",
    }
    art = build_artifact_record(
        run_id=run_id,
        stage_key="candidate_selection",
        artifact_key="candidate_selection_summary",
        artifact_type="candidate_selection_summary",
        data=summary_data,
        summary={"stage_status": stage_status, "total_selected": len(candidates)},
    )
    put_artifact(store, art, overwrite=True)
    return summary_data


def _populate_all_upstream(store, run_id, **kwargs):
    """Populate store with all upstream artifacts (Steps 4, 5, 7)."""
    _write_market_artifacts(store, run_id, **kwargs.get("market", {}))
    _write_model_artifacts(store, run_id, **kwargs.get("model", {}))
    _write_selection_artifacts(store, run_id, **kwargs.get("selection", {}))


# =====================================================================
#  Constants and vocabulary
# =====================================================================

class TestConstants:
    """Verify module constants are correct."""

    def test_stage_key(self):
        assert _STAGE_KEY == "shared_context"
        assert _STAGE_KEY in PIPELINE_STAGES

    def test_context_modules(self):
        assert _MODULE_MARKET in _CONTEXT_MODULES
        assert _MODULE_MODEL in _CONTEXT_MODULES
        assert _MODULE_SELECTION in _CONTEXT_MODULES
        assert len(_CONTEXT_MODULES) == 3

    def test_assembly_statuses(self):
        assert ASSEMBLY_STATUS_FULL == "full"
        assert ASSEMBLY_STATUS_DEGRADED == "degraded"
        assert ASSEMBLY_STATUS_FAILED == "failed"

    def test_event_types_registered(self):
        for evt in (
            "context_assembly_started",
            "context_assembly_completed",
            "context_assembly_failed",
        ):
            assert evt in VALID_EVENT_TYPES, f"{evt} not in VALID_EVENT_TYPES"

    def test_artifact_types_registered(self):
        for at in ("shared_context", "context_assembly_summary"):
            assert at in VALID_ARTIFACT_TYPES, f"{at} not in VALID_ARTIFACT_TYPES"


# =====================================================================
#  Upstream retrieval tests
# =====================================================================

class TestRetrieveMarketModule:
    """Test _retrieve_market_module."""

    def test_full_retrieval(self):
        store = create_artifact_store("r1")
        _write_market_artifacts(store, "r1", engine_keys=("breadth", "vol"))
        result = _retrieve_market_module(store)

        assert result["available"] is True
        assert result["stage_status"] == "success"
        assert result["summary"] is not None
        assert set(result["engine_keys"]) == {"breadth", "vol"}
        assert "breadth" in result["engines"]
        assert "vol" in result["engines"]
        assert result["degraded_reasons"] == []

    def test_missing_summary(self):
        store = create_artifact_store("r1")
        result = _retrieve_market_module(store)

        assert result["available"] is False
        assert result["stage_status"] is None
        assert result["engine_keys"] == []
        assert result["engines"] == {}
        assert len(result["degraded_reasons"]) == 1
        assert "missing" in result["degraded_reasons"][0].lower()

    def test_partial_engines(self):
        """Summary references engines but some artifacts missing."""
        store = create_artifact_store("r1")
        # Write summary claiming 2 engines, but only write 1 artifact
        summary_data = {
            "stage_key": "market_data",
            "stage_status": "degraded",
            "engines_succeeded": ["breadth", "volatility"],
            "degraded_reasons": ["one engine had issues"],
        }
        art = build_artifact_record(
            run_id="r1",
            stage_key="market_data",
            artifact_key="market_stage_summary",
            artifact_type="market_stage_summary",
            data=summary_data,
        )
        put_artifact(store, art, overwrite=True)

        # Write only breadth engine artifact
        eng_art = build_artifact_record(
            run_id="r1",
            stage_key="market_data",
            artifact_key="engine_breadth",
            artifact_type="market_engine_output",
            data={"engine_key": "breadth", "score": 65},
        )
        put_artifact(store, eng_art, overwrite=True)

        result = _retrieve_market_module(store)
        assert result["available"] is True
        assert "breadth" in result["engines"]
        assert "volatility" not in result["engines"]
        assert any("volatility" in r for r in result["degraded_reasons"])


class TestRetrieveModelModule:
    """Test _retrieve_model_module."""

    def test_full_retrieval(self):
        store = create_artifact_store("r1")
        _write_model_artifacts(store, "r1", engine_keys=("breadth",))
        result = _retrieve_model_module(store)

        assert result["available"] is True
        assert result["stage_status"] == "success"
        assert result["engine_keys"] == ["breadth"]
        assert "breadth" in result["models"]
        assert result["degraded_reasons"] == []

    def test_missing_summary(self):
        store = create_artifact_store("r1")
        result = _retrieve_model_module(store)

        assert result["available"] is False
        assert result["models"] == {}

    def test_degraded_upstream(self):
        store = create_artifact_store("r1")
        _write_model_artifacts(
            store, "r1",
            stage_status="degraded",
            degraded_reasons=["one model timed out"],
        )
        result = _retrieve_model_module(store)

        assert result["available"] is True
        assert result["stage_status"] == "degraded"
        assert "one model timed out" in result["degraded_reasons"]


class TestRetrieveSelectionModule:
    """Test _retrieve_selection_module."""

    def test_full_retrieval(self):
        store = create_artifact_store("r1")
        cands = [{"candidate_id": "c1"}, {"candidate_id": "c2"}]
        _write_selection_artifacts(store, "r1", candidates=cands)
        result = _retrieve_selection_module(store)

        assert result["available"] is True
        assert result["selected_count"] == 2
        assert len(result["selected_candidates"]) == 2

    def test_missing_candidates(self):
        store = create_artifact_store("r1")
        result = _retrieve_selection_module(store)

        assert result["available"] is False
        assert result["selected_candidates"] == []
        assert result["selected_count"] == 0

    def test_missing_summary_but_has_candidates(self):
        """Candidates exist but summary is missing → degraded."""
        store = create_artifact_store("r1")
        art = build_artifact_record(
            run_id="r1",
            stage_key="candidate_selection",
            artifact_key="selected_candidates",
            artifact_type="selected_candidate",
            data=[{"candidate_id": "c1"}],
        )
        put_artifact(store, art, overwrite=True)

        result = _retrieve_selection_module(store)
        assert result["available"] is True
        assert result["selected_count"] == 1
        assert any("summary" in r.lower() for r in result["degraded_reasons"])


# =====================================================================
#  Module record builder tests
# =====================================================================

class TestBuildModuleRecord:
    """Test _build_module_record."""

    def test_full_module(self):
        retrieval = {
            "available": True,
            "stage_status": "success",
            "degraded_reasons": [],
        }
        rec = _build_module_record("market_data", retrieval, elapsed_ms=5)

        assert rec["module_name"] == "market_data"
        assert rec["available"] is True
        assert rec["assembly_status"] == ASSEMBLY_STATUS_FULL
        assert rec["elapsed_ms"] == 5

    def test_degraded_module(self):
        retrieval = {
            "available": True,
            "stage_status": "degraded",
            "degraded_reasons": ["engine timeout"],
        }
        rec = _build_module_record("model_analysis", retrieval)
        assert rec["assembly_status"] == ASSEMBLY_STATUS_DEGRADED

    def test_failed_module(self):
        retrieval = {
            "available": False,
            "stage_status": None,
            "degraded_reasons": ["artifact missing"],
        }
        rec = _build_module_record("candidate_selection", retrieval)
        assert rec["assembly_status"] == ASSEMBLY_STATUS_FAILED
        assert rec["available"] is False


# =====================================================================
#  Overall assembly status tests
# =====================================================================

class TestComputeOverallAssemblyStatus:
    """Test _compute_overall_assembly_status."""

    def test_all_full(self):
        records = [
            {"module_name": "a", "assembly_status": ASSEMBLY_STATUS_FULL},
            {"module_name": "b", "assembly_status": ASSEMBLY_STATUS_FULL},
            {"module_name": "c", "assembly_status": ASSEMBLY_STATUS_FULL},
        ]
        assert _compute_overall_assembly_status(records) == ASSEMBLY_STATUS_FULL

    def test_one_degraded(self):
        records = [
            {"module_name": "a", "assembly_status": ASSEMBLY_STATUS_FULL},
            {"module_name": "b", "assembly_status": ASSEMBLY_STATUS_DEGRADED},
            {"module_name": "c", "assembly_status": ASSEMBLY_STATUS_FULL},
        ]
        assert _compute_overall_assembly_status(records) == ASSEMBLY_STATUS_DEGRADED

    def test_one_failed(self):
        records = [
            {"module_name": "a", "assembly_status": ASSEMBLY_STATUS_FULL},
            {"module_name": "b", "assembly_status": ASSEMBLY_STATUS_FAILED},
            {"module_name": "c", "assembly_status": ASSEMBLY_STATUS_FULL},
        ]
        assert _compute_overall_assembly_status(records) == ASSEMBLY_STATUS_FAILED

    def test_failed_trumps_degraded(self):
        records = [
            {"module_name": "a", "assembly_status": ASSEMBLY_STATUS_DEGRADED},
            {"module_name": "b", "assembly_status": ASSEMBLY_STATUS_FAILED},
        ]
        assert _compute_overall_assembly_status(records) == ASSEMBLY_STATUS_FAILED


# =====================================================================
#  Full assembly tests
# =====================================================================

class TestAssembleSharedContext:
    """Test assemble_shared_context."""

    def test_full_assembly(self):
        store = create_artifact_store("r1")
        _populate_all_upstream(store, "r1")
        ctx = assemble_shared_context(store)

        assert ctx["overall_status"] == ASSEMBLY_STATUS_FULL
        assert len(ctx["module_records"]) == 3
        assert ctx["degraded_reasons"] == []
        assert "assembled_at" in ctx

        modules = ctx["context_modules"]
        assert _MODULE_MARKET in modules
        assert _MODULE_MODEL in modules
        assert _MODULE_SELECTION in modules

    def test_market_module_shape(self):
        store = create_artifact_store("r1")
        _populate_all_upstream(store, "r1")
        ctx = assemble_shared_context(store)
        market = ctx["context_modules"][_MODULE_MARKET]

        assert market["available"] is True
        assert market["summary"] is not None
        assert isinstance(market["engine_keys"], list)
        assert isinstance(market["engines"], dict)

    def test_model_module_shape(self):
        store = create_artifact_store("r1")
        _populate_all_upstream(store, "r1")
        ctx = assemble_shared_context(store)
        model = ctx["context_modules"][_MODULE_MODEL]

        assert model["available"] is True
        assert model["summary"] is not None
        assert isinstance(model["engine_keys"], list)
        assert isinstance(model["models"], dict)

    def test_selection_module_shape(self):
        store = create_artifact_store("r1")
        _populate_all_upstream(store, "r1")
        ctx = assemble_shared_context(store)
        sel = ctx["context_modules"][_MODULE_SELECTION]

        assert sel["available"] is True
        assert isinstance(sel["selected_candidates"], list)
        assert sel["selected_count"] >= 1

    def test_degraded_when_market_missing(self):
        store = create_artifact_store("r1")
        # Only write model + selection, skip market
        _write_model_artifacts(store, "r1")
        _write_selection_artifacts(store, "r1")
        ctx = assemble_shared_context(store)

        assert ctx["overall_status"] == ASSEMBLY_STATUS_FAILED
        market_mod = ctx["context_modules"][_MODULE_MARKET]
        assert market_mod["available"] is False

    def test_degraded_when_model_degraded(self):
        store = create_artifact_store("r1")
        _write_market_artifacts(store, "r1")
        _write_model_artifacts(
            store, "r1",
            stage_status="degraded",
            degraded_reasons=["timeout"],
        )
        _write_selection_artifacts(store, "r1")
        ctx = assemble_shared_context(store)

        assert ctx["overall_status"] == ASSEMBLY_STATUS_DEGRADED
        assert any("timeout" in r for r in ctx["degraded_reasons"])

    def test_empty_store(self):
        store = create_artifact_store("r1")
        ctx = assemble_shared_context(store)

        assert ctx["overall_status"] == ASSEMBLY_STATUS_FAILED
        for rec in ctx["module_records"]:
            assert rec["assembly_status"] == ASSEMBLY_STATUS_FAILED


# =====================================================================
#  Artifact creation tests
# =====================================================================

class TestArtifactCreation:
    """Test artifact writing by the handler."""

    def test_shared_context_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        art = get_artifact_by_key(store, "shared_context", "shared_context")
        assert art is not None
        assert art["artifact_type"] == "shared_context"
        data = art["data"]
        assert "context_modules" in data
        assert "module_records" in data
        assert "overall_status" in data

    def test_summary_artifact_written(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        art = get_artifact_by_key(
            store, "shared_context", "context_assembly_summary",
        )
        assert art is not None
        assert art["artifact_type"] == "context_assembly_summary"
        data = art["data"]
        assert "stage_key" in data
        assert "overall_status" in data
        assert "modules_assembled" in data
        assert "module_records" in data

    def test_artifact_refs_in_summary(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        summary_art = get_artifact_by_key(
            store, "shared_context", "context_assembly_summary",
        )
        data = summary_art["data"]
        assert data["shared_context_artifact_ref"] is not None
        assert data["summary_artifact_ref"] is not None

    def test_stage_artifacts_count(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        context_assembly_handler(run, store, "shared_context")

        arts = list_stage_artifacts(store, "shared_context")
        assert len(arts) == 2  # shared_context + context_assembly_summary


# =====================================================================
#  Stage summary tests
# =====================================================================

class TestStageSummary:
    """Test the stage summary in metadata."""

    def test_summary_fields(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")
        summary = result["metadata"]["stage_summary"]

        assert summary["stage_key"] == "shared_context"
        assert summary["overall_status"] == ASSEMBLY_STATUS_FULL
        assert summary["modules_assembled"] == 3
        assert summary["modules_full"] == 3
        assert summary["modules_degraded"] == 0
        assert summary["modules_failed"] == 0
        assert isinstance(summary["module_records"], list)
        assert summary["elapsed_ms"] >= 0
        assert "generated_at" in summary

    def test_summary_with_degraded_module(self):
        run, store = _make_run_and_store()
        _write_market_artifacts(store, run["run_id"])
        _write_model_artifacts(
            store, run["run_id"],
            stage_status="degraded",
            degraded_reasons=["slow model"],
        )
        _write_selection_artifacts(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")
        summary = result["metadata"]["stage_summary"]

        assert summary["overall_status"] == ASSEMBLY_STATUS_DEGRADED
        assert summary["modules_degraded"] >= 1
        assert len(summary["degraded_reasons"]) >= 1


# =====================================================================
#  Event emission tests
# =====================================================================

class TestEventEmission:
    """Test structured event emission."""

    def test_started_event(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])
        events = []

        mark_stage_running(run, "shared_context")
        context_assembly_handler(
            run, store, "shared_context",
            event_callback=events.append,
        )

        started = [e for e in events if e["event_type"] == "context_assembly_started"]
        assert len(started) == 1

    def test_completed_event(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])
        events = []

        mark_stage_running(run, "shared_context")
        context_assembly_handler(
            run, store, "shared_context",
            event_callback=events.append,
        )

        completed = [e for e in events if e["event_type"] == "context_assembly_completed"]
        assert len(completed) == 1
        meta = completed[0].get("metadata", {})
        assert "overall_status" in meta
        assert "modules_full" in meta

    def test_failed_event_on_all_missing(self):
        run, store = _make_run_and_store()
        events = []

        mark_stage_running(run, "shared_context")
        context_assembly_handler(
            run, store, "shared_context",
            event_callback=events.append,
        )

        failed = [e for e in events if e["event_type"] == "context_assembly_failed"]
        assert len(failed) == 1

    def test_no_events_without_callback(self):
        """Handler should work fine without event_callback."""
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")
        assert result["outcome"] == "completed"


# =====================================================================
#  Handler contract tests
# =====================================================================

class TestHandlerContract:
    """Test the handler return dict shape."""

    def test_successful_result_shape(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        assert result["outcome"] == "completed"
        assert "summary_counts" in result
        assert "artifacts" in result
        assert "metadata" in result
        assert result["error"] is None

    def test_summary_counts_keys(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        sc = result["summary_counts"]
        assert "modules_assembled" in sc
        assert "modules_degraded" in sc
        assert "modules_failed" in sc
        assert sc["modules_assembled"] == 3
        assert sc["modules_degraded"] == 0
        assert sc["modules_failed"] == 0

    def test_metadata_keys(self):
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        meta = result["metadata"]
        assert "overall_status" in meta
        assert "module_records" in meta
        assert "stage_summary" in meta
        assert "shared_context_artifact_id" in meta
        assert "summary_artifact_id" in meta
        assert "elapsed_ms" in meta

    def test_failed_result_shape(self):
        run, store = _make_run_and_store()
        # Empty store → all modules fail

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        assert result["outcome"] == "failed"
        assert result["error"] is not None
        assert result["error"]["code"] == "CONTEXT_ASSEMBLY_FAILED"
        assert result["summary_counts"]["modules_failed"] == 3

    def test_failed_result_includes_module_names(self):
        """Error message and metadata include which modules failed."""
        run, store = _make_run_and_store()
        # Write only market and model — selection missing
        _write_market_artifacts(store, run["run_id"])
        _write_model_artifacts(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        assert result["outcome"] == "failed"
        # Error message should name the failing module
        assert "candidate_selection" in result["error"]["message"]
        # Metadata should list failed module names
        assert "failed_module_names" in result["metadata"]
        assert "candidate_selection" in result["metadata"]["failed_module_names"]
        # Error detail should list failed modules
        assert "candidate_selection" in result["error"]["detail"]["failed_modules"]

    def test_failed_event_includes_module_names(self):
        """The context_assembly_failed event names failing modules."""
        run, store = _make_run_and_store()
        _write_market_artifacts(store, run["run_id"])
        _write_model_artifacts(store, run["run_id"])

        events_captured: list[dict] = []

        def _cb(event: dict) -> None:
            events_captured.append(event)

        mark_stage_running(run, "shared_context")
        context_assembly_handler(
            run, store, "shared_context", event_callback=_cb,
        )

        failed_events = [
            e for e in events_captured
            if e.get("event_type") == "context_assembly_failed"
        ]
        assert len(failed_events) == 1
        msg = failed_events[0].get("message", "")
        assert "candidate_selection" in msg


# =====================================================================
#  Degraded assembly tests
# =====================================================================

class TestDegradedAssembly:
    """Test degraded assembly behavior."""

    def test_partial_data_still_completes(self):
        """Missing model → failed module but handler still reports failed overall."""
        run, store = _make_run_and_store()
        _write_market_artifacts(store, run["run_id"])
        _write_selection_artifacts(store, run["run_id"])
        # Model artifacts missing

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        # At least one module failed, so overall is failed
        assert result["outcome"] == "failed"
        assert result["summary_counts"]["modules_failed"] >= 1

    def test_degraded_market_propagates(self):
        """Degraded market → overall degraded, outcome still completed."""
        run, store = _make_run_and_store()
        _write_market_artifacts(
            store, run["run_id"],
            stage_status="degraded",
            degraded_reasons=["one engine failed"],
        )
        _write_model_artifacts(store, run["run_id"])
        _write_selection_artifacts(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        assert result["outcome"] == "completed"
        assert result["metadata"]["overall_status"] == ASSEMBLY_STATUS_DEGRADED

    def test_all_degraded_still_completes(self):
        run, store = _make_run_and_store()
        _write_market_artifacts(
            store, run["run_id"],
            stage_status="degraded",
            degraded_reasons=["market issue"],
        )
        _write_model_artifacts(
            store, run["run_id"],
            stage_status="degraded",
            degraded_reasons=["model issue"],
        )
        _write_selection_artifacts(
            store, run["run_id"],
            stage_status="degraded",
            degraded_reasons=["selection issue"],
        )

        mark_stage_running(run, "shared_context")
        result = context_assembly_handler(run, store, "shared_context")

        assert result["outcome"] == "completed"
        assert result["metadata"]["overall_status"] == ASSEMBLY_STATUS_DEGRADED
        assert result["summary_counts"]["modules_degraded"] == 3


# =====================================================================
#  Orchestrator integration tests
# =====================================================================

def _success_handler(run, artifact_store, stage_key, **kwargs):
    """Minimal success handler for stubbing stages."""
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
    """Test integration with the orchestrator."""

    def test_default_handler_wired(self):
        handlers = get_default_handlers()
        assert handlers["shared_context"] is context_assembly_handler

    def test_runs_through_pipeline_with_stubs(self):
        """Full pipeline with stub handlers completes."""
        result = _all_stub_pipeline()
        run = result["run"]
        assert run["status"] in ("completed", "partial_failed")

    def test_execute_stage_with_handler(self):
        """Execute just the shared_context stage via execute_stage."""
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        result = execute_stage(
            run, store, "shared_context",
            handler=context_assembly_handler,
        )
        assert result["outcome"] == "completed"
        assert result["artifact_count"] == 0  # handler writes directly

    def test_dependency_gating(self):
        """shared_context depends on market_model_analysis AND candidate_selection.
        If market_model_analysis is not completed, stage should be skipped."""
        run = create_pipeline_run(run_id="dep-test")
        store = create_artifact_store("dep-test")
        # Only complete market_data, not market_model_analysis
        mark_stage_running(run, "market_data")
        mark_stage_completed(run, "market_data")

        result = execute_stage(
            run, store, "shared_context",
            handler=context_assembly_handler,
        )
        assert result["outcome"] == "skipped"
        assert "unsatisfied" in result["skipped_reason"].lower()

    def test_dependency_requires_candidate_selection(self):
        """shared_context also depends on candidate_selection.
        If candidate_selection is not completed, stage is skipped."""
        run = create_pipeline_run(run_id="dep-sel-test")
        store = create_artifact_store("dep-sel-test")
        # Complete market_data AND market_model_analysis but NOT candidate_selection
        for stage in ("market_data", "market_model_analysis", "stock_scanners", "options_scanners"):
            mark_stage_running(run, stage)
            mark_stage_completed(run, stage)

        result = execute_stage(
            run, store, "shared_context",
            handler=context_assembly_handler,
        )
        assert result["outcome"] == "skipped"
        assert "unsatisfied" in result["skipped_reason"].lower()

    def test_dependency_satisfied_with_both_deps(self):
        """shared_context runs when both market_model_analysis AND
        candidate_selection are completed."""
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        result = execute_stage(
            run, store, "shared_context",
            handler=context_assembly_handler,
        )
        assert result["outcome"] == "completed"
        assert result["dependency_status"] == "satisfied"


# =====================================================================
#  Forward compatibility / retrieval seam tests
# =====================================================================

class TestForwardCompatibility:
    """Verify downstream retrieval seams work."""

    def test_shared_context_retrievable_by_key(self):
        """Downstream enrichment can retrieve shared_context by key."""
        run, store = _make_run_and_store()
        _populate_all_upstream(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        context_assembly_handler(run, store, "shared_context")

        art = get_artifact_by_key(store, "shared_context", "shared_context")
        assert art is not None
        data = art["data"]
        assert "context_modules" in data
        assert _MODULE_MARKET in data["context_modules"]
        assert _MODULE_MODEL in data["context_modules"]
        assert _MODULE_SELECTION in data["context_modules"]

    def test_selected_candidates_in_context(self):
        """Selected candidates are accessible in shared context."""
        run, store = _make_run_and_store()
        cands = [
            {"candidate_id": "c1", "symbol": "SPY"},
            {"candidate_id": "c2", "symbol": "QQQ"},
        ]
        _write_market_artifacts(store, run["run_id"])
        _write_model_artifacts(store, run["run_id"])
        _write_selection_artifacts(store, run["run_id"], candidates=cands)

        mark_stage_running(run, "shared_context")
        context_assembly_handler(run, store, "shared_context")

        art = get_artifact_by_key(store, "shared_context", "shared_context")
        sel = art["data"]["context_modules"][_MODULE_SELECTION]
        assert sel["selected_count"] == 2
        assert len(sel["selected_candidates"]) == 2

    def test_engine_data_accessible(self):
        """Engine data is accessible in the market context module."""
        run, store = _make_run_and_store()
        _write_market_artifacts(
            store, run["run_id"],
            engine_keys=("breadth",),
            engine_data={"breadth": {"engine_key": "breadth", "score": 80}},
        )
        _write_model_artifacts(store, run["run_id"])
        _write_selection_artifacts(store, run["run_id"])

        mark_stage_running(run, "shared_context")
        context_assembly_handler(run, store, "shared_context")

        art = get_artifact_by_key(store, "shared_context", "shared_context")
        market = art["data"]["context_modules"][_MODULE_MARKET]
        assert "breadth" in market["engines"]
        assert market["engines"]["breadth"]["score"] == 80
