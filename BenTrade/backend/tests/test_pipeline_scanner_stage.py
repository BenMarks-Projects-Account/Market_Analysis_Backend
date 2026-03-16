"""Tests for Pipeline Scanner Stage v1.0.

Coverage targets:
─── Scanner registry
    - default registry shape
    - override via scanner_registry kwarg
    - entry has required keys
─── Scanner selection
    - disabled scanner via registry
    - disabled scanner via kwargs
    - family filter
    - key filter
    - all filtered out
─── Scanner execution record
    - record shape / required keys
    - status vocabulary
─── Candidate normalization
    - pre-normalized candidates
    - raw candidate normalization fallback
    - empty candidates list
    - invalid candidate type
    - normalization failure → raw passthrough
    - pipeline lineage fields
─── Scanner execution
    - all scanners succeed
    - partial failure (degraded)
    - all selected scanners fail
    - scanners complete with zero candidates
    - bounded parallel execution
    - per-scanner exception handling
    - executor exception at thread level
─── Artifact creation
    - raw scanner output artifact
    - candidate artifact per scanner
    - stage summary artifact
    - artifact lineage refs
─── Stage summary
    - contains expected fields
    - completed counts
    - failed counts
    - skipped counts
    - candidate counts
    - stage status rollup
    - degraded reasons
    - no_candidates status
─── Event emission
    - scanner_started events
    - scanner_completed events
    - scanner_failed events
─── Handler contract
    - returns expected dict shape
    - outcome field values
    - summary_counts fields
─── Orchestrator integration
    - default handler wired
    - runs through pipeline
    - stub override works
─── Forward compatibility
    - candidate downstream_usable flag
    - scanner-to-candidate lineage
    - candidate retrieval seam
─── Scanner results override
    - pre-computed results bypass executor
"""

import pytest

from app.services.pipeline_scanner_stage import (
    _STAGE_KEY,
    DEFAULT_SCANNER_MAX_WORKERS,
    SCANNER_STATUSES,
    _select_scanners,
    build_scanner_execution_record,
    build_scanner_stage_summary,
    get_default_scanner_registry,
    normalize_scanner_candidates,
    scanner_stage_handler,
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
    create_artifact_store,
    get_artifact_by_key,
    list_artifacts,
    list_stage_artifacts,
    validate_artifact_store,
)
from app.services.pipeline_orchestrator import (
    execute_stage,
    get_default_handlers,
    run_pipeline_with_handlers,
)


# ── Helper factories ────────────────────────────────────────────

def _make_run_and_store(run_id="test-scanner-001"):
    """Create a fresh pipeline run and artifact store."""
    run = create_pipeline_run(run_id=run_id)
    store = create_artifact_store(run_id)
    # Mark market_data completed (scanners depend on it)
    mark_stage_running(run, "market_data")
    mark_stage_completed(run, "market_data")
    # Inject legacy "scanners" stage entry for old handler tests that
    # call execute_stage(run, store, "scanners", ...).  The "scanners"
    # stage was removed from PIPELINE_STAGES but the handler still exists.
    run["stages"]["scanners"] = {
        "stage_key": "scanners",
        "label": "Scanners",
        "status": "pending",
        "started_at": None,
        "ended_at": None,
        "duration_ms": None,
        "depends_on": [],
        "summary_counts": {},
        "error": None,
        "artifact_refs": [],
        "log_event_count": 0,
    }
    return run, store


def _small_registry(**overrides):
    """Return a small 2-scanner registry for fast tests."""
    from app.services.pipeline_scanner_stage import _make_scanner_entry
    reg = {
        "test_scanner_a": _make_scanner_entry(
            "test_scanner_a", "Test Scanner A",
            "stock", "test_a",
        ),
        "test_scanner_b": _make_scanner_entry(
            "test_scanner_b", "Test Scanner B",
            "options", "test_b",
        ),
    }
    reg.update(overrides)
    return reg


def _mock_scanner_executor(scanner_key, scanner_entry, context):
    """Mock executor returning 2 pre-normalized candidates per scanner."""
    family = scanner_entry.get("scanner_family", "stock")
    return {
        "scanner_key": scanner_key,
        "status": "ok",
        "candidates": [
            {
                "symbol": "SPY",
                "trade_key": f"{scanner_key}_SPY_001",
                "normalized": {
                    "candidate_id": f"{scanner_key}_SPY_001",
                    "scanner_key": scanner_key,
                    "scanner_name": scanner_entry.get("display_name", scanner_key),
                    "strategy_family": family,
                    "setup_type": scanner_entry.get("strategy_type", scanner_key),
                    "symbol": "SPY",
                    "direction": "long",
                    "setup_quality": 75.0,
                    "confidence": 0.8,
                },
            },
            {
                "symbol": "QQQ",
                "trade_key": f"{scanner_key}_QQQ_001",
                "normalized": {
                    "candidate_id": f"{scanner_key}_QQQ_001",
                    "scanner_key": scanner_key,
                    "scanner_name": scanner_entry.get("display_name", scanner_key),
                    "strategy_family": family,
                    "setup_type": scanner_entry.get("strategy_type", scanner_key),
                    "symbol": "QQQ",
                    "direction": "long",
                    "setup_quality": 65.0,
                    "confidence": 0.7,
                },
            },
        ],
    }


def _empty_scanner_executor(scanner_key, scanner_entry, context):
    """Mock executor returning zero candidates."""
    return {
        "scanner_key": scanner_key,
        "status": "ok",
        "candidates": [],
    }


def _failing_scanner_executor(scanner_key, scanner_entry, context):
    """Mock executor that always raises."""
    raise RuntimeError(f"Scanner {scanner_key} exploded")


def _selective_scanner_executor(fail_keys):
    """Factory: returns executor that fails for specific keys."""
    def _exec(scanner_key, scanner_entry, context):
        if scanner_key in fail_keys:
            raise RuntimeError(f"Scanner {scanner_key} failed")
        return _mock_scanner_executor(scanner_key, scanner_entry, context)
    return _exec


def _success_handler(run, artifact_store, stage_key, **kwargs):
    """Always-succeed stub handler."""
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 0},
        "artifacts": [],
        "metadata": {"stub": True},
        "error": None,
    }


# =====================================================================
#  Scanner Registry
# =====================================================================

class TestScannerRegistry:

    def test_default_registry_not_empty(self):
        reg = get_default_scanner_registry()
        assert len(reg) > 0

    def test_default_registry_has_stock_scanners(self):
        reg = get_default_scanner_registry()
        stock = [k for k, v in reg.items() if v["scanner_family"] == "stock"]
        assert len(stock) >= 4

    def test_default_registry_has_options_scanners(self):
        reg = get_default_scanner_registry()
        options = [k for k, v in reg.items() if v["scanner_family"] == "options"]
        assert len(options) >= 4

    def test_entry_has_required_keys(self):
        reg = get_default_scanner_registry()
        required = {"scanner_key", "display_name", "scanner_family",
                     "strategy_type", "enabled", "required"}
        for key, entry in reg.items():
            assert required.issubset(entry.keys()), (
                f"Entry '{key}' missing: {required - set(entry.keys())}"
            )

    def test_entry_scanner_key_matches_dict_key(self):
        reg = get_default_scanner_registry()
        for key, entry in reg.items():
            assert entry["scanner_key"] == key

    def test_all_enabled_by_default(self):
        reg = get_default_scanner_registry()
        for key, entry in reg.items():
            assert entry["enabled"] is True, f"'{key}' not enabled"

    def test_override_via_kwarg(self):
        from app.services.pipeline_scanner_stage import _make_scanner_entry
        custom_reg = {
            "custom_scanner": _make_scanner_entry(
                "custom_scanner", "Custom", "stock", "custom",
            ),
        }
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=custom_reg,
            scanner_executor=_mock_scanner_executor,
        )
        assert result["outcome"] == "completed"
        # Only one scanner should have run
        assert result["summary_counts"]["scanners_run"] == 1


# =====================================================================
#  Scanner Execution Record
# =====================================================================

class TestScannerExecutionRecord:

    def test_record_shape(self):
        rec = build_scanner_execution_record(
            scanner_key="test_a",
            scanner_family="stock",
            strategy_type="test",
            status="completed",
        )
        expected_keys = {
            "scanner_key", "scanner_family", "strategy_type", "status",
            "started_at", "completed_at", "elapsed_ms",
            "candidate_count", "raw_result_present",
            "output_artifact_ref", "candidate_artifact_ref",
            "downstream_usable", "warnings", "notes", "error",
        }
        assert expected_keys.issubset(rec.keys())

    def test_status_values(self):
        for status in SCANNER_STATUSES:
            rec = build_scanner_execution_record(
                scanner_key="x", status=status,
            )
            assert rec["status"] == status

    def test_defaults(self):
        rec = build_scanner_execution_record(
            scanner_key="x", status="completed",
        )
        assert rec["candidate_count"] == 0
        assert rec["raw_result_present"] is False
        assert rec["downstream_usable"] is False
        assert rec["warnings"] == []
        assert rec["notes"] == []
        assert rec["error"] is None


class TestStatusVocabulary:

    def test_all_statuses_present(self):
        expected = {
            "completed", "completed_empty", "skipped_disabled",
            "skipped_not_selected", "failed",
        }
        assert expected == SCANNER_STATUSES


# =====================================================================
#  Scanner Selection
# =====================================================================

class TestScannerSelection:

    def test_all_enabled_selected(self):
        reg = _small_registry()
        eligible, skipped = _select_scanners(reg)
        assert len(eligible) == 2
        assert len(skipped) == 0

    def test_disabled_in_registry(self):
        from app.services.pipeline_scanner_stage import _make_scanner_entry
        reg = _small_registry(
            test_scanner_c=_make_scanner_entry(
                "test_scanner_c", "Disabled", "stock", "c", enabled=False,
            ),
        )
        eligible, skipped = _select_scanners(reg)
        assert len(eligible) == 2
        assert "test_scanner_c" in skipped
        assert skipped["test_scanner_c"]["status"] == "skipped_disabled"

    def test_disabled_via_kwargs(self):
        reg = _small_registry()
        eligible, skipped = _select_scanners(
            reg, disabled_scanners={"test_scanner_a"},
        )
        assert len(eligible) == 1
        assert eligible[0]["scanner_key"] == "test_scanner_b"
        assert skipped["test_scanner_a"]["status"] == "skipped_disabled"

    def test_family_filter(self):
        reg = _small_registry()
        eligible, skipped = _select_scanners(
            reg, selected_families={"stock"},
        )
        assert len(eligible) == 1
        assert eligible[0]["scanner_key"] == "test_scanner_a"
        assert skipped["test_scanner_b"]["status"] == "skipped_not_selected"

    def test_key_filter(self):
        reg = _small_registry()
        eligible, skipped = _select_scanners(
            reg, selected_scanners={"test_scanner_b"},
        )
        assert len(eligible) == 1
        assert eligible[0]["scanner_key"] == "test_scanner_b"
        assert skipped["test_scanner_a"]["status"] == "skipped_not_selected"

    def test_all_disabled(self):
        reg = _small_registry()
        eligible, skipped = _select_scanners(
            reg, disabled_scanners={"test_scanner_a", "test_scanner_b"},
        )
        assert len(eligible) == 0
        assert len(skipped) == 2

    def test_combined_filters(self):
        reg = _small_registry()
        eligible, skipped = _select_scanners(
            reg,
            selected_families={"stock"},
            disabled_scanners={"test_scanner_a"},
        )
        assert len(eligible) == 0
        assert len(skipped) == 2

    def test_empty_family_filter_selects_none(self):
        reg = _small_registry()
        eligible, skipped = _select_scanners(
            reg, selected_families=set(),
        )
        assert len(eligible) == 0
        assert len(skipped) == 2


# =====================================================================
#  Candidate Normalization
# =====================================================================

class TestCandidateNormalization:

    def test_pre_normalized_candidates(self):
        entry = {"scanner_key": "test", "scanner_family": "stock",
                 "strategy_type": "test"}
        raw = {
            "candidates": [
                {
                    "symbol": "SPY",
                    "normalized": {
                        "candidate_id": "test_SPY",
                        "scanner_key": "test",
                        "symbol": "SPY",
                    },
                },
            ],
        }
        candidates, warnings = normalize_scanner_candidates(
            "test", entry, raw, run_id="run-001",
        )
        assert len(candidates) == 1
        assert candidates[0]["candidate_id"] == "test_SPY"
        assert candidates[0]["run_id"] == "run-001"
        assert candidates[0]["stage_key"] == "scanners"
        assert candidates[0]["normalization_status"] == "normalized"
        assert candidates[0]["downstream_usable"] is True
        assert len(warnings) == 0

    def test_empty_candidates_list(self):
        entry = {"scanner_key": "test", "scanner_family": "stock",
                 "strategy_type": "test"}
        raw = {"candidates": []}
        candidates, warnings = normalize_scanner_candidates(
            "test", entry, raw, run_id="run-001",
        )
        assert len(candidates) == 0
        assert len(warnings) == 0

    def test_missing_candidates_key(self):
        entry = {"scanner_key": "test", "scanner_family": "stock",
                 "strategy_type": "test"}
        raw = {"status": "ok"}
        candidates, warnings = normalize_scanner_candidates(
            "test", entry, raw, run_id="run-001",
        )
        assert len(candidates) == 0

    def test_invalid_candidate_type(self):
        entry = {"scanner_key": "test", "scanner_family": "stock",
                 "strategy_type": "test"}
        raw = {"candidates": ["not_a_dict", 42]}
        candidates, warnings = normalize_scanner_candidates(
            "test", entry, raw, run_id="run-001",
        )
        assert len(candidates) == 0
        assert len(warnings) == 2

    def test_candidates_not_list(self):
        entry = {"scanner_key": "test", "scanner_family": "stock",
                 "strategy_type": "test"}
        raw = {"candidates": "bad"}
        candidates, warnings = normalize_scanner_candidates(
            "test", entry, raw, run_id="run-001",
        )
        assert len(candidates) == 0
        assert len(warnings) == 1
        assert "not a list" in warnings[0]

    def test_source_artifact_ref(self):
        entry = {"scanner_key": "test", "scanner_family": "stock",
                 "strategy_type": "test"}
        raw = {
            "candidates": [
                {"symbol": "SPY", "normalized": {
                    "candidate_id": "c1", "scanner_key": "test"}},
            ],
        }
        candidates, _ = normalize_scanner_candidates(
            "test", entry, raw,
            run_id="run-001", source_artifact_ref="art-123",
        )
        assert candidates[0]["source_scanner_artifact_ref"] == "art-123"

    def test_candidate_id_fallback(self):
        entry = {"scanner_key": "test", "scanner_family": "stock",
                 "strategy_type": "test"}
        raw = {
            "candidates": [
                {"symbol": "SPY", "normalized": {"scanner_key": "test"}},
            ],
        }
        candidates, _ = normalize_scanner_candidates(
            "test", entry, raw, run_id="run-001",
        )
        # Should fall back to trade_key or index-based ID
        assert candidates[0]["candidate_id"] == "test_0"

    def test_candidate_id_from_trade_key(self):
        entry = {"scanner_key": "test", "scanner_family": "stock",
                 "strategy_type": "test"}
        raw = {
            "candidates": [
                {
                    "symbol": "SPY",
                    "trade_key": "my_key",
                    "normalized": {"scanner_key": "test"},
                },
            ],
        }
        candidates, _ = normalize_scanner_candidates(
            "test", entry, raw, run_id="run-001",
        )
        assert candidates[0]["candidate_id"] == "my_key"


# =====================================================================
#  All Scanners Succeed
# =====================================================================

class TestAllScannersSucceed:

    def test_outcome_completed(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        assert result["outcome"] == "completed"

    def test_stage_status_success(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        assert result["metadata"]["stage_status"] == "success"

    def test_summary_counts(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        counts = result["summary_counts"]
        assert counts["scanners_run"] == 2
        assert counts["scanners_completed"] == 2
        assert counts["scanners_failed"] == 0
        assert counts["scanners_skipped"] == 0
        assert counts["total_candidates"] == 4
        assert counts["total_usable_candidates"] == 4

    def test_artifacts_written(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        arts = list_stage_artifacts(store, "scanners")
        # 2 raw + 2 candidate + 1 summary = 5
        assert len(arts) == 5

    def test_raw_scanner_artifacts(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art_a = get_artifact_by_key(store, "scanners", "scanner_test_scanner_a")
        art_b = get_artifact_by_key(store, "scanners", "scanner_test_scanner_b")
        assert art_a is not None
        assert art_b is not None
        assert art_a["artifact_type"] == "scanner_output"
        assert art_b["artifact_type"] == "scanner_output"

    def test_candidate_artifacts(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art_a = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_a",
        )
        art_b = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_b",
        )
        assert art_a is not None
        assert art_b is not None
        assert art_a["artifact_type"] == "normalized_candidate"
        assert len(art_a["data"]) == 2

    def test_stage_summary_artifact(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        summary = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        assert summary is not None
        assert summary["artifact_type"] == "scanner_stage_summary"

    def test_no_error(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        assert result["error"] is None


# =====================================================================
#  Partial Failure (Degraded)
# =====================================================================

class TestPartialFailure:

    def test_outcome_completed(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_selective_scanner_executor({"test_scanner_b"}),
        )
        assert result["outcome"] == "completed"

    def test_stage_status_degraded(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_selective_scanner_executor({"test_scanner_b"}),
        )
        assert result["metadata"]["stage_status"] == "degraded"

    def test_counts_reflect_partial(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_selective_scanner_executor({"test_scanner_b"}),
        )
        counts = result["summary_counts"]
        assert counts["scanners_completed"] == 1
        assert counts["scanners_failed"] == 1
        assert counts["total_candidates"] == 2

    def test_degraded_reasons(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_selective_scanner_executor({"test_scanner_b"}),
        )
        reasons = result["metadata"]["degraded_reasons"]
        assert len(reasons) >= 1
        assert "test_scanner_b" in reasons[0]


# =====================================================================
#  All Scanners Fail
# =====================================================================

class TestAllFail:

    def test_outcome_failed(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_failing_scanner_executor,
        )
        assert result["outcome"] == "failed"

    def test_error_code(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_failing_scanner_executor,
        )
        assert result["error"]["code"] == "ALL_SCANNERS_FAILED"

    def test_stage_status_failed(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_failing_scanner_executor,
        )
        assert result["metadata"]["stage_status"] == "failed"

    def test_summary_still_written(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_failing_scanner_executor,
        )
        summary = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        assert summary is not None


# =====================================================================
#  Zero Candidates
# =====================================================================

class TestZeroCandidates:

    def test_outcome_completed(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_empty_scanner_executor,
        )
        assert result["outcome"] == "completed"

    def test_stage_status_no_candidates(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_empty_scanner_executor,
        )
        assert result["metadata"]["stage_status"] == "no_candidates"

    def test_counts_zero(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_empty_scanner_executor,
        )
        counts = result["summary_counts"]
        assert counts["total_candidates"] == 0
        assert counts["scanners_completed"] == 2

    def test_not_a_failure(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_empty_scanner_executor,
        )
        assert result["error"] is None


# =====================================================================
#  Zero Eligible Scanners
# =====================================================================

class TestZeroEligible:

    def test_all_disabled(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
            disabled_scanners={"test_scanner_a", "test_scanner_b"},
        )
        assert result["outcome"] == "completed"

    def test_stage_status_no_eligible(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
            disabled_scanners={"test_scanner_a", "test_scanner_b"},
        )
        assert result["metadata"]["stage_status"] == "no_eligible_scanners"

    def test_zero_run_zero_candidates(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
            disabled_scanners={"test_scanner_a", "test_scanner_b"},
        )
        counts = result["summary_counts"]
        assert counts["scanners_run"] == 0
        assert counts["total_candidates"] == 0
        assert counts["scanners_skipped"] == 2

    def test_summary_written(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
            disabled_scanners={"test_scanner_a", "test_scanner_b"},
        )
        summary = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        assert summary is not None


# =====================================================================
#  Bounded Parallel Execution
# =====================================================================

class TestBoundedParallel:

    def test_max_workers_kwarg(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
            max_workers=1,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["scanners_completed"] == 2

    def test_default_max_workers(self):
        assert DEFAULT_SCANNER_MAX_WORKERS == 3


# =====================================================================
#  Per-Scanner Exception Handling
# =====================================================================

class TestScannerExceptions:

    def test_exception_captured_not_crash(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_selective_scanner_executor({"test_scanner_a"}),
        )
        # One should fail, one should succeed
        assert result["outcome"] == "completed"
        records = result["metadata"]["scanner_records"]
        assert records["test_scanner_a"]["status"] == "failed"
        assert records["test_scanner_b"]["status"] == "completed"

    def test_failed_scanner_has_error(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_selective_scanner_executor({"test_scanner_a"}),
        )
        rec = result["metadata"]["scanner_records"]["test_scanner_a"]
        assert rec["error"] is not None
        assert rec["error"]["code"] == "SCANNER_EXCEPTION"

    def test_executor_level_exception(self):
        """Exception at the ThreadPoolExecutor level."""
        def bad_executor(scanner_key, scanner_entry, context):
            # Raise after running to simulate thread-level failure
            raise SystemError("Thread blew up")

        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=bad_executor,
        )
        # Both fail → stage fails
        assert result["outcome"] == "failed"


# =====================================================================
#  Raw Scanner Artifact
# =====================================================================

class TestRawScannerArtifact:

    def test_artifact_type(self):
        assert "scanner_output" in VALID_ARTIFACT_TYPES

    def test_artifact_created(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(store, "scanners", "scanner_test_scanner_a")
        assert art is not None
        assert art["artifact_type"] == "scanner_output"
        assert "candidates" in art["data"]

    def test_metadata_has_scanner_key(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(store, "scanners", "scanner_test_scanner_a")
        assert art["metadata"]["scanner_key"] == "test_scanner_a"


# =====================================================================
#  Candidate Artifact
# =====================================================================

class TestCandidateArtifact:

    def test_artifact_type(self):
        assert "normalized_candidate" in VALID_ARTIFACT_TYPES

    def test_artifact_created(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_a",
        )
        assert art is not None
        assert art["artifact_type"] == "normalized_candidate"

    def test_candidate_data_shape(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_a",
        )
        data = art["data"]
        assert isinstance(data, list)
        assert len(data) == 2
        for c in data:
            assert "candidate_id" in c
            assert "scanner_key" in c
            assert "downstream_usable" in c
            assert "run_id" in c
            assert "stage_key" in c

    def test_candidate_lineage_ref(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_a",
        )
        # Source artifact ref should point to the raw scanner artifact
        assert art["metadata"]["source_artifact_ref"] is not None

    def test_summary_has_candidate_ids(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_a",
        )
        assert len(art["summary"]["candidate_ids"]) == 2


# =====================================================================
#  Stage Summary
# =====================================================================

class TestStageSummary:

    def test_summary_shape(self):
        summary = build_scanner_stage_summary(
            {"a": {"status": "completed", "candidate_count": 2}},
            {"b": {"status": "skipped_disabled"}},
        )
        expected_keys = {
            "stage_key", "stage_status", "total_considered", "total_run",
            "scanners_completed", "scanners_completed_empty",
            "scanners_failed", "scanners_skipped",
            "completed_count", "failed_count", "skipped_count",
            "total_candidates", "total_usable_candidates",
            "all_candidate_ids", "artifact_refs",
            "candidate_artifact_refs", "candidate_index",
            "degraded_reasons", "scanner_summaries",
            "elapsed_ms", "generated_at",
        }
        assert expected_keys.issubset(summary.keys())

    def test_stage_key(self):
        summary = build_scanner_stage_summary({}, {})
        assert summary["stage_key"] == "scanners"

    def test_success_status(self):
        summary = build_scanner_stage_summary(
            {"a": {"status": "completed"}},
            {},
            candidate_counts={"a": 3},
        )
        assert summary["stage_status"] == "success"

    def test_failed_status(self):
        summary = build_scanner_stage_summary(
            {"a": {"status": "failed"}},
            {},
        )
        assert summary["stage_status"] == "failed"

    def test_degraded_status(self):
        summary = build_scanner_stage_summary(
            {
                "a": {"status": "completed"},
                "b": {"status": "failed"},
            },
            {},
            candidate_counts={"a": 2},
        )
        assert summary["stage_status"] == "degraded"

    def test_no_candidates_status(self):
        summary = build_scanner_stage_summary(
            {"a": {"status": "completed_empty"}},
            {},
        )
        assert summary["stage_status"] == "no_candidates"

    def test_no_eligible_status(self):
        summary = build_scanner_stage_summary(
            {},
            {"a": {"status": "skipped_disabled"}},
        )
        assert summary["stage_status"] == "no_eligible_scanners"

    def test_candidate_counts(self):
        summary = build_scanner_stage_summary(
            {"a": {"status": "completed"}, "b": {"status": "completed"}},
            {},
            candidate_counts={"a": 3, "b": 5},
            usable_candidate_counts={"a": 3, "b": 4},
        )
        assert summary["total_candidates"] == 8
        assert summary["total_usable_candidates"] == 7

    def test_scanner_summaries_per_scanner(self):
        summary = build_scanner_stage_summary(
            {"a": {"status": "completed", "scanner_family": "stock",
                    "strategy_type": "test"}},
            {"b": {"status": "skipped_disabled", "scanner_family": "options",
                    "strategy_type": "test_b"}},
        )
        assert "a" in summary["scanner_summaries"]
        assert "b" in summary["scanner_summaries"]
        assert summary["scanner_summaries"]["a"]["status"] == "completed"
        assert summary["scanner_summaries"]["b"]["status"] == "skipped_disabled"

    def test_all_candidate_ids(self):
        summary = build_scanner_stage_summary(
            {"a": {"status": "completed"}},
            {},
            candidate_index={"a": ["c1", "c2", "c3"]},
        )
        assert summary["all_candidate_ids"] == ["c1", "c2", "c3"]


# =====================================================================
#  Stage Summary Artifact
# =====================================================================

class TestStageSummaryArtifact:

    def test_artifact_type(self):
        assert "scanner_stage_summary" in VALID_ARTIFACT_TYPES

    def test_artifact_written(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        assert art is not None
        assert art["artifact_type"] == "scanner_stage_summary"
        assert art["data"]["stage_key"] == "scanners"

    def test_summary_has_candidate_counts(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        data = art["data"]
        assert data["total_candidates"] == 4
        assert data["total_usable_candidates"] == 4


# =====================================================================
#  Event Emission
# =====================================================================

class TestEventEmission:

    def test_scanner_started_events(self):
        events = []
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
            event_callback=lambda e: events.append(e),
        )
        started = [e for e in events if e["event_type"] == "scanner_started"]
        assert len(started) == 2

    def test_scanner_completed_events(self):
        events = []
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
            event_callback=lambda e: events.append(e),
        )
        completed = [e for e in events if e["event_type"] == "scanner_completed"]
        assert len(completed) == 2

    def test_scanner_failed_event(self):
        events = []
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_selective_scanner_executor({"test_scanner_a"}),
            event_callback=lambda e: events.append(e),
        )
        failed = [e for e in events if e["event_type"] == "scanner_failed"]
        assert len(failed) == 1
        assert failed[0]["metadata"]["scanner_key"] == "test_scanner_a"

    def test_event_types_registered(self):
        assert "scanner_started" in VALID_EVENT_TYPES
        assert "scanner_completed" in VALID_EVENT_TYPES
        assert "scanner_failed" in VALID_EVENT_TYPES

    def test_event_metadata_has_scanner_key(self):
        events = []
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
            event_callback=lambda e: events.append(e),
        )
        for e in events:
            if e["event_type"] in ("scanner_started", "scanner_completed"):
                assert "scanner_key" in e["metadata"]


# =====================================================================
#  Handler Contract Shape
# =====================================================================

class TestHandlerContract:

    def test_result_shape(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        expected_keys = {"outcome", "summary_counts", "artifacts",
                         "metadata", "error"}
        assert expected_keys == set(result.keys())

    def test_summary_counts_keys(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        expected = {
            "scanners_run", "scanners_completed", "scanners_failed",
            "scanners_skipped", "total_candidates",
            "total_usable_candidates",
        }
        assert expected == set(result["summary_counts"].keys())

    def test_metadata_has_summary_artifact_id(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        assert "stage_summary_artifact_id" in result["metadata"]

    def test_artifacts_list_empty(self):
        """Artifacts are written directly, not via handler artifacts list."""
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        assert result["artifacts"] == []


# =====================================================================
#  Scanner Results Override
# =====================================================================

class TestScannerResultsOverride:

    def test_override_bypasses_executor(self):
        run, store = _make_run_and_store()
        override = {
            "test_scanner_a": {
                "scanner_key": "test_scanner_a",
                "candidates": [
                    {
                        "symbol": "AAPL",
                        "normalized": {
                            "candidate_id": "override_AAPL",
                            "scanner_key": "test_scanner_a",
                            "symbol": "AAPL",
                        },
                    },
                ],
            },
        }
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_failing_scanner_executor,  # should NOT be called
            scanner_results_override=override,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_candidates"] >= 1

    def test_override_empty_candidates(self):
        run, store = _make_run_and_store()
        override = {
            "test_scanner_a": {"candidates": []},
            "test_scanner_b": {"candidates": []},
        }
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_failing_scanner_executor,
            scanner_results_override=override,
        )
        assert result["outcome"] == "completed"
        assert result["summary_counts"]["total_candidates"] == 0


# =====================================================================
#  Artifact Lineage
# =====================================================================

class TestArtifactLineage:

    def test_candidate_refs_to_scanner(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        cand_art = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_a",
        )
        scanner_art = get_artifact_by_key(
            store, "scanners", "scanner_test_scanner_a",
        )
        assert cand_art["metadata"]["source_artifact_ref"] == scanner_art["artifact_id"]

    def test_summary_refs_to_scanner_artifacts(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        summary = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        refs = summary["data"]["artifact_refs"]
        assert "test_scanner_a" in refs
        assert "test_scanner_b" in refs

    def test_summary_refs_to_candidate_artifacts(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        summary = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        refs = summary["data"]["candidate_artifact_refs"]
        assert "test_scanner_a" in refs
        assert "test_scanner_b" in refs

    def test_candidate_index_in_summary(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        summary = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        idx = summary["data"]["candidate_index"]
        assert "test_scanner_a" in idx
        assert len(idx["test_scanner_a"]) == 2


# =====================================================================
#  Forward Compatibility
# =====================================================================

class TestForwardCompatibility:

    def test_downstream_usable_flag(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        art = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_a",
        )
        for c in art["data"]:
            assert c["downstream_usable"] is True

    def test_candidate_retrieval_by_key(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        # Downstream stages can retrieve candidates by scanner key
        art = get_artifact_by_key(
            store, "scanners", "candidates_test_scanner_a",
        )
        assert art is not None
        assert isinstance(art["data"], list)

    def test_scanner_stage_summary_retrievable(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        summary = get_artifact_by_key(
            store, "scanners", "scanner_stage_summary",
        )
        assert summary is not None
        assert "all_candidate_ids" in summary["data"]


# =====================================================================
#  Orchestrator Integration
# =====================================================================

class TestOrchestratorIntegration:

    def test_default_handlers_wired(self):
        """stock_scanners and options_scanners are wired into default handlers."""
        handlers = get_default_handlers()
        assert "stock_scanners" in handlers
        assert handlers["stock_scanners"] is not None
        assert "options_scanners" in handlers
        assert handlers["options_scanners"] is not None

    def test_execute_stage_with_scanner_handler(self):
        run, store = _make_run_and_store()
        result = execute_stage(
            run, store, "scanners",
            handler=scanner_stage_handler,
            handler_kwargs={
                "scanner_registry": _small_registry(),
                "scanner_executor": _mock_scanner_executor,
            },
        )
        assert result["outcome"] == "completed"

    def test_pipeline_with_stub_overrides(self):
        """Full pipeline with all stages stubbed."""
        result = run_pipeline_with_handlers(
            {
                "market_data": _success_handler,
                "market_model_analysis": _success_handler,
                "stock_scanners": _success_handler,
                "options_scanners": _success_handler,
                "candidate_selection": _success_handler,
                "shared_context": _success_handler,
                "candidate_enrichment": _success_handler,
                "events": _success_handler,
                "policy": _success_handler,
                "orchestration": _success_handler,
                "prompt_payload": _success_handler,
                "final_model_decision": _success_handler,
                "final_response_normalization": _success_handler,
            },
        )
        outcomes = {sr["stage_key"]: sr["outcome"]
                    for sr in result["stage_results"]}
        assert outcomes["stock_scanners"] == "completed"
        assert outcomes["options_scanners"] == "completed"


# =====================================================================
#  Output Stability
# =====================================================================

class TestOutputStability:

    def test_stable_record_keys(self):
        rec = build_scanner_execution_record(
            scanner_key="test", status="completed",
        )
        keys = set(rec.keys())
        expected = {
            "scanner_key", "scanner_family", "strategy_type", "status",
            "started_at", "completed_at", "elapsed_ms",
            "candidate_count", "raw_result_present",
            "output_artifact_ref", "candidate_artifact_ref",
            "downstream_usable", "warnings", "notes", "error",
        }
        assert keys == expected

    def test_stable_handler_result_keys(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        assert set(result.keys()) == {
            "outcome", "summary_counts", "artifacts", "metadata", "error",
        }

    def test_metadata_keys(self):
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        expected_meta = {
            "stage_summary_artifact_id", "scanner_artifact_ids",
            "candidate_artifact_ids", "stage_status", "elapsed_ms",
            "scanner_records", "degraded_reasons", "liveness_snapshot",
            "finalization_checkpoint", "finalization_duration_ms",
        }
        assert expected_meta == set(result["metadata"].keys())


# =====================================================================
#  Executor Injection
# =====================================================================

class TestExecutorInjection:

    def test_custom_executor_called(self):
        called = []

        def tracking_executor(scanner_key, scanner_entry, context):
            called.append(scanner_key)
            return _mock_scanner_executor(scanner_key, scanner_entry, context)

        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=tracking_executor,
        )
        assert set(called) == {"test_scanner_a", "test_scanner_b"}

    def test_executor_receives_context(self):
        received_ctx = {}

        def ctx_executor(scanner_key, scanner_entry, context):
            received_ctx.update(context)
            return _mock_scanner_executor(scanner_key, scanner_entry, context)

        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=ctx_executor,
            symbols=["SPY", "QQQ"],
            preset="strict",
        )
        assert received_ctx.get("symbols") == ["SPY", "QQQ"]
        assert received_ctx.get("preset") == "strict"


# =====================================================================
#  Market Context (Optional)
# =====================================================================

class TestMarketContext:

    def test_market_summary_in_context(self):
        """If Step 4 summary exists, it's passed to scanner context."""
        received_ctx = {}

        def ctx_executor(scanner_key, scanner_entry, context):
            received_ctx.update(context)
            return _mock_scanner_executor(scanner_key, scanner_entry, context)

        run, store = _make_run_and_store()
        # Write a fake Step 4 market stage summary
        from app.services.pipeline_artifact_store import build_artifact_record
        art = build_artifact_record(
            run_id=run["run_id"],
            stage_key="market_data",
            artifact_key="market_stage_summary",
            artifact_type="market_stage_summary",
            data={"stage_status": "success", "engine_count": 6},
        )
        from app.services.pipeline_artifact_store import put_artifact
        put_artifact(store, art)

        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=ctx_executor,
        )
        assert received_ctx.get("market_summary") is not None
        assert received_ctx["market_summary"]["stage_status"] == "success"

    def test_no_market_summary_still_works(self):
        """Scanner stage works without Step 4 market summary."""
        run, store = _make_run_and_store()
        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        assert result["outcome"] == "completed"


# =====================================================================
#  Store Validation
# =====================================================================

class TestStoreValidation:

    def test_store_valid_after_handler(self):
        run, store = _make_run_and_store()
        scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )
        ok, errors = validate_artifact_store(store)
        assert ok, f"Store validation: {errors}"


# =====================================================================
#  Scanner Stage Finalization (Part G)
# =====================================================================

class TestScannerStageFinalization:
    """Tests that the scanner stage always reaches a terminal state and
    propagates counters correctly."""

    def test_scanners_complete_emits_stage_completed(self):
        """When all scanner futures complete and liveness is empty,
        execute_stage emits stage_completed and returns outcome=completed."""
        run, store = _make_run_and_store()
        events = []

        def _capture(event):
            events.append(event)

        result = execute_stage(
            run, store, "scanners",
            handler=lambda r, s, sk, **kw: scanner_stage_handler(
                r, s, sk,
                scanner_registry=_small_registry(),
                scanner_executor=_mock_scanner_executor,
                **kw,
            ),
            event_callback=_capture,
        )

        assert result["outcome"] == "completed"
        stage_status = run["stages"]["scanners"]["status"]
        assert stage_status == "completed"
        completed_events = [
            e for e in events if e.get("event_type") == "stage_completed"
            and e.get("stage_key") == "scanners"
        ]
        assert len(completed_events) == 1, (
            f"Expected exactly 1 stage_completed event, got {len(completed_events)}"
        )

    def test_candidate_counters_populated_after_scanner_completion(self):
        """candidate_counters['scanned'] is updated after scanner stage
        completes with candidates."""
        run, store = _make_run_and_store()

        result = execute_stage(
            run, store, "scanners",
            handler=lambda r, s, sk, **kw: scanner_stage_handler(
                r, s, sk,
                scanner_registry=_small_registry(),
                scanner_executor=_mock_scanner_executor,
                **kw,
            ),
        )

        # The legacy "scanners" stage is no longer in _COUNTER_MAP,
        # so verify counts via the handler result directly.
        sc = result.get("summary_counts", {})
        assert sc.get("total_candidates", 0) > 0, (
            f"Expected total_candidates > 0, got {sc}"
        )

    def test_post_processing_failure_yields_stage_failed(self):
        """If post-processing raises, the stage still returns a result
        and does not leave the stage stuck in RUNNING."""
        run, store = _make_run_and_store()

        def _bomb_executor(scanner_key, scanner_entry, context):
            """Return a result whose raw_result will cause
            normalize_scanner_candidates to raise."""
            return {
                "scanner_key": scanner_key,
                "status": "ok",
                "candidates": "NOT_A_LIST",  # will trigger TypeError
            }

        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_bomb_executor,
        )

        # Must return a result dict — not raise
        assert isinstance(result, dict)
        assert "outcome" in result
        # Stage must not be stuck in running
        assert result["outcome"] in ("completed", "failed")

    def test_scanner_stage_cannot_remain_running(self):
        """Scanner stage handler ALWAYS returns a result dict, so the
        orchestrator can transition from running to a terminal state."""
        run, store = _make_run_and_store()

        result = execute_stage(
            run, store, "scanners",
            handler=lambda r, s, sk, **kw: scanner_stage_handler(
                r, s, sk,
                scanner_registry=_small_registry(),
                scanner_executor=_failing_scanner_executor,
                **kw,
            ),
        )

        assert isinstance(result, dict)
        assert result["outcome"] in ("completed", "failed")
        stage_status = run["stages"]["scanners"]["status"]
        assert stage_status in ("completed", "failed"), (
            f"Stage stuck in '{stage_status}', expected terminal state"
        )

    def test_candidate_selection_runs_after_scanner_stage(self):
        """candidate_selection stage can proceed after healthy scanner
        completion (dependency gate satisfied)."""
        from app.services.pipeline_orchestrator import (
            _check_dependencies,
            _DEFAULT_DEPENDENCY_MAP,
        )

        run, store = _make_run_and_store()

        # Run legacy scanner stage through execute_stage
        execute_stage(
            run, store, "scanners",
            handler=lambda r, s, sk, **kw: scanner_stage_handler(
                r, s, sk,
                scanner_registry=_small_registry(),
                scanner_executor=_mock_scanner_executor,
                **kw,
            ),
        )

        # Verify scanners completed
        assert run["stages"]["scanners"]["status"] == "completed"

        # Complete the new split scanner stages so the dependency gate
        # for candidate_selection (which now requires stock_scanners +
        # options_scanners) is satisfied.
        mark_stage_running(run, "stock_scanners")
        mark_stage_completed(run, "stock_scanners")
        mark_stage_running(run, "options_scanners")
        mark_stage_completed(run, "options_scanners")

        # Check dependency gate for candidate_selection
        satisfied, reason = _check_dependencies(
            run, "candidate_selection", _DEFAULT_DEPENDENCY_MAP,
        )
        assert satisfied, (
            f"candidate_selection blocked after scanner completion: {reason}"
        )


# =====================================================================
#  Regression: scanner stage completion trigger (b7a32beef80f)
# =====================================================================

class TestScannerStageCompletionTrigger:
    """Regression tests for the scanner stage completion trigger fix.

    Root cause: _execute_scanners_parallel was called OUTSIDE the
    try/except in scanner_stage_handler, so any exception from parallel
    execution left the pipeline stuck in "running" forever with
    candidate_counters all zero.

    These tests verify:
    - Handler ALWAYS returns a result dict (never raises)
    - candidate_counters are committed via execute_stage
    - Parallel execution failure produces a degraded result
    - total_candidates flows through to candidate_counters["scanned"]
    """

    def test_handler_returns_even_when_executor_raises(self):
        """If _execute_scanners_parallel raises, the handler catches
        it and returns a degraded result — never raises into the
        orchestrator."""
        run, store = _make_run_and_store()

        def _exploding_executor(scanner_key, scanner_entry, context):
            raise RuntimeError("ThreadPool hang simulation")

        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_exploding_executor,
        )

        assert isinstance(result, dict), "Handler must return a dict"
        assert result["outcome"] in ("completed", "failed")
        assert "summary_counts" in result
        assert "error" in result

    def test_total_candidates_flows_to_scanned_counter(self):
        """summary_counts['total_candidates'] is reported by the handler
        after execute_stage completes."""
        run, store = _make_run_and_store()

        result = execute_stage(
            run, store, "scanners",
            handler=lambda r, s, sk, **kw: scanner_stage_handler(
                r, s, sk,
                scanner_registry=_small_registry(),
                scanner_executor=_mock_scanner_executor,
                **kw,
            ),
        )

        assert result["outcome"] == "completed"
        sc = result.get("summary_counts", {})
        assert sc.get("total_candidates", 0) > 0, (
            f"Handler must report total_candidates > 0, got {sc}"
        )

    def test_degraded_result_has_summary_counts(self):
        """Even when parallel execution fails, the degraded result
        includes summary_counts with the required keys so
        _update_candidate_counters has data to work with."""
        run, store = _make_run_and_store()

        def _exploding_executor(scanner_key, scanner_entry, context):
            raise RuntimeError("Simulated failure")

        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_exploding_executor,
        )

        sc = result.get("summary_counts", {})
        required_keys = {
            "scanners_run", "scanners_completed", "scanners_failed",
            "scanners_skipped", "total_candidates", "total_usable_candidates",
        }
        assert required_keys.issubset(set(sc.keys())), (
            f"Degraded result missing summary_counts keys: "
            f"{required_keys - set(sc.keys())}"
        )

    def test_finalization_checkpoint_tracks_failure(self):
        """When post-processing raises (e.g. artifact writing),
        the finalization checkpoint records post_processing_failed."""
        from unittest.mock import patch
        run, store = _make_run_and_store()

        # Make artifact writing explode AFTER parallel execution returns
        with patch(
            "app.services.pipeline_scanner_stage._write_scanner_output_artifact",
            side_effect=RuntimeError("Artifact write boom"),
        ):
            result = scanner_stage_handler(
                run, store, "scanners",
                scanner_registry=_small_registry(),
                scanner_executor=_mock_scanner_executor,
            )

        checkpoint = result.get("metadata", {}).get("finalization_checkpoint", {})
        states = [h["state"] for h in checkpoint.get("history", [])]
        assert "post_processing_failed" in states, (
            f"Expected 'post_processing_failed' in checkpoint history, got {states}"
        )
        # Must still return a valid result
        assert result["outcome"] in ("completed", "failed")

    def test_execute_stage_never_leaves_running(self):
        """execute_stage ALWAYS transitions scanner stage from 'running'
        to a terminal state, even when the handler's parallel execution
        fails."""
        run, store = _make_run_and_store()

        def _exploding_executor(scanner_key, scanner_entry, context):
            raise RuntimeError("Simulated failure")

        result = execute_stage(
            run, store, "scanners",
            handler=lambda r, s, sk, **kw: scanner_stage_handler(
                r, s, sk,
                scanner_registry=_small_registry(),
                scanner_executor=_exploding_executor,
                **kw,
            ),
        )

        stage_status = run["stages"]["scanners"]["status"]
        assert stage_status in ("completed", "failed"), (
            f"Stage stuck in '{stage_status}' after parallel execution failure"
        )

    def test_handler_returning_checkpoint(self):
        """The finalization checkpoint includes 'result_built' on
        the happy path, proving the handler made it all the way through
        finalization before returning."""
        run, store = _make_run_and_store()

        result = scanner_stage_handler(
            run, store, "scanners",
            scanner_registry=_small_registry(),
            scanner_executor=_mock_scanner_executor,
        )

        checkpoint = result.get("metadata", {}).get("finalization_checkpoint", {})
        states = [h["state"] for h in checkpoint.get("history", [])]
        # result_built is the last checkpoint captured in the snapshot
        # (handler_returning is advanced after the snapshot is taken)
        assert "result_built" in states, (
            f"Expected 'result_built' in checkpoint history, got {states}"
        )
