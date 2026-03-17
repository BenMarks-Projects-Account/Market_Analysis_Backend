"""Tests for artifact_strategy module.

Focused tests only — no broad regression, no archived pipeline code.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.workflows.artifact_strategy import (
    MANIFEST_FILENAME,
    MANIFEST_REQUIRED_KEYS,
    MARKET_STATE_DIR_NAME,
    OPTIONS_WORKFLOW_DIR,
    OUTPUT_ARTIFACT_REQUIRED_KEYS,
    OUTPUT_FILENAME,
    POINTER_FILENAME,
    RUN_ID_PREFIX,
    SHORT_HEX_LENGTH,
    STAGE_ARTIFACT_REQUIRED_KEYS,
    STOCK_WORKFLOW_DIR,
    SUMMARY_FILENAME,
    TIMESTAMP_FORMAT,
    WORKFLOW_ARTIFACT_EXPECTATIONS,
    WORKFLOW_POINTER_REQUIRED_KEYS,
    WORKFLOWS_DIR_NAME,
    ArtifactCategory,
    ManifestStageEntry,
    WorkflowArtifactExpectation,
    WorkflowPointerData,
    atomic_write_json,
    get_manifest_path,
    get_output_path,
    get_pointer_path,
    get_run_dir,
    get_stage_artifact_path,
    get_summary_path,
    get_workflow_dir,
    make_run_id,
    make_stage_filename,
    write_workflow_pointer,
    LINEAGE_REQUIRED_KEYS,
    DEFAULT_RETENTION_DAYS,
)

NOW = datetime(2026, 3, 16, 15, 0, 0, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════
# Directory constants
# ═══════════════════════════════════════════════════════════════════════


class TestDirectoryConstants:
    def test_workflows_dir(self):
        assert WORKFLOWS_DIR_NAME == "workflows"

    def test_stock_dir(self):
        assert STOCK_WORKFLOW_DIR == "stock_opportunity"

    def test_options_dir(self):
        assert OPTIONS_WORKFLOW_DIR == "options_opportunity"

    def test_market_state_dir(self):
        assert MARKET_STATE_DIR_NAME == "market_state"

    def test_pointer_filename(self):
        assert POINTER_FILENAME == "latest.json"

    def test_manifest_filename(self):
        assert MANIFEST_FILENAME == "manifest.json"

    def test_output_filename(self):
        assert OUTPUT_FILENAME == "output.json"

    def test_summary_filename(self):
        assert SUMMARY_FILENAME == "summary.json"


# ═══════════════════════════════════════════════════════════════════════
# Run ID & naming conventions
# ═══════════════════════════════════════════════════════════════════════


class TestRunID:
    def test_format(self):
        run_id = make_run_id(NOW)
        assert run_id.startswith("run_20260316_150000_")
        # Short hex suffix
        suffix = run_id.split("_")[-1]
        assert len(suffix) == SHORT_HEX_LENGTH

    def test_uniqueness(self):
        ids = {make_run_id(NOW) for _ in range(20)}
        assert len(ids) == 20  # all unique due to uuid hex

    def test_sortable(self):
        earlier = datetime(2026, 3, 16, 14, 0, 0, tzinfo=timezone.utc)
        later = datetime(2026, 3, 16, 16, 0, 0, tzinfo=timezone.utc)
        id1 = make_run_id(earlier)
        id2 = make_run_id(later)
        assert id1 < id2  # lexicographic order = chronological

    def test_default_timestamp(self):
        run_id = make_run_id()
        assert run_id.startswith(RUN_ID_PREFIX)


class TestStageFilename:
    def test_format(self):
        assert make_stage_filename("scan") == "stage_scan.json"

    def test_compound_key(self):
        assert make_stage_filename("enrich_evaluate") == "stage_enrich_evaluate.json"

    def test_load_market_state(self):
        assert make_stage_filename("load_market_state") == "stage_load_market_state.json"


# ═══════════════════════════════════════════════════════════════════════
# Artifact categories
# ═══════════════════════════════════════════════════════════════════════


class TestArtifactCategories:
    def test_six_categories(self):
        assert len(ArtifactCategory) == 6

    def test_str_enum(self):
        assert isinstance(ArtifactCategory.PUBLISHED_STATE, str)
        assert ArtifactCategory.FINAL_OUTPUT == "final_output"

    @pytest.mark.parametrize("cat", list(ArtifactCategory))
    def test_all_are_strings(self, cat):
        assert isinstance(cat, str)


# ═══════════════════════════════════════════════════════════════════════
# Lineage / required keys
# ═══════════════════════════════════════════════════════════════════════


class TestRequiredKeys:
    def test_lineage_keys(self):
        assert "workflow_id" in LINEAGE_REQUIRED_KEYS
        assert "run_id" in LINEAGE_REQUIRED_KEYS
        assert "generated_at" in LINEAGE_REQUIRED_KEYS

    def test_stage_artifact_keys(self):
        for k in ("stage_key", "stage_index", "status"):
            assert k in STAGE_ARTIFACT_REQUIRED_KEYS

    def test_output_artifact_keys(self):
        for k in ("market_state_ref", "candidates", "publication"):
            assert k in OUTPUT_ARTIFACT_REQUIRED_KEYS

    def test_manifest_keys(self):
        for k in ("stages", "output_filename", "started_at", "completed_at"):
            assert k in MANIFEST_REQUIRED_KEYS


# ═══════════════════════════════════════════════════════════════════════
# Path builders
# ═══════════════════════════════════════════════════════════════════════


class TestPathBuilders:
    def test_market_intelligence_dir(self):
        d = get_workflow_dir("/data", "market_intelligence")
        assert d == Path("/data/market_state")

    def test_stock_dir(self):
        d = get_workflow_dir("/data", "stock_opportunity")
        assert d == Path("/data/workflows/stock_opportunity")

    def test_options_dir(self):
        d = get_workflow_dir("/data", "options_opportunity")
        assert d == Path("/data/workflows/options_opportunity")

    def test_run_dir(self):
        d = get_run_dir("/data", "stock_opportunity", "run_20260316_150000_abcd")
        assert d == Path("/data/workflows/stock_opportunity/run_20260316_150000_abcd")

    def test_stage_path(self):
        p = get_stage_artifact_path(
            "/data", "stock_opportunity", "run_x", "scan"
        )
        assert p.name == "stage_scan.json"
        assert "run_x" in str(p)

    def test_output_path(self):
        p = get_output_path("/data", "stock_opportunity", "run_x")
        assert p.name == OUTPUT_FILENAME

    def test_summary_path(self):
        p = get_summary_path("/data", "stock_opportunity", "run_x")
        assert p.name == SUMMARY_FILENAME

    def test_manifest_path(self):
        p = get_manifest_path("/data", "options_opportunity", "run_x")
        assert p.name == MANIFEST_FILENAME

    def test_pointer_path_stock(self):
        p = get_pointer_path("/data", "stock_opportunity")
        assert p.name == POINTER_FILENAME
        assert "stock_opportunity" in str(p)

    def test_pointer_path_market_intel(self):
        p = get_pointer_path("/data", "market_intelligence")
        assert p.name == POINTER_FILENAME
        assert "market_state" in str(p)


# ═══════════════════════════════════════════════════════════════════════
# Atomic write
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicWrite:
    def test_writes_valid_json(self, tmp_path):
        target = tmp_path / "test.json"
        atomic_write_json(target, {"key": "value"})
        assert target.is_file()
        content = json.loads(target.read_text(encoding="utf-8"))
        assert content["key"] == "value"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "deep" / "test.json"
        atomic_write_json(target, {"x": 1})
        assert target.is_file()

    def test_no_tmp_file_left(self, tmp_path):
        target = tmp_path / "test.json"
        atomic_write_json(target, {"x": 1})
        tmp_file = target.with_suffix(".json.tmp")
        assert not tmp_file.exists()

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "test.json"
        atomic_write_json(target, {"v": 1})
        atomic_write_json(target, {"v": 2})
        content = json.loads(target.read_text(encoding="utf-8"))
        assert content["v"] == 2


# ═══════════════════════════════════════════════════════════════════════
# Manifest stage entry
# ═══════════════════════════════════════════════════════════════════════


class TestManifestStageEntry:
    def test_to_dict(self):
        entry = ManifestStageEntry(
            stage_key="scan",
            stage_index=1,
            status="completed",
            artifact_filename="stage_scan.json",
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
        )
        d = entry.to_dict()
        assert d["stage_key"] == "scan"
        assert d["stage_index"] == 1
        assert "record_count" not in d

    def test_to_dict_with_record_count(self):
        entry = ManifestStageEntry(
            stage_key="scan",
            stage_index=1,
            status="completed",
            artifact_filename="stage_scan.json",
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
            record_count=42,
        )
        d = entry.to_dict()
        assert d["record_count"] == 42

    def test_frozen(self):
        entry = ManifestStageEntry(
            stage_key="x", stage_index=0, status="ok",
            artifact_filename=None, started_at=None, completed_at=None,
        )
        with pytest.raises(AttributeError):
            entry.stage_key = "y"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# Workflow pointer
# ═══════════════════════════════════════════════════════════════════════


class TestWorkflowPointer:
    def test_roundtrip(self):
        p = WorkflowPointerData(
            run_id="run_20260316_150000_abcd",
            workflow_id="stock_opportunity",
            completed_at=NOW.isoformat(),
            status="valid",
            output_filename="output.json",
            contract_version="1.0",
        )
        d = p.to_dict()
        p2 = WorkflowPointerData.from_dict(d)
        assert p == p2

    def test_from_dict_missing_key(self):
        with pytest.raises(KeyError):
            WorkflowPointerData.from_dict({"run_id": "x"})

    def test_write_and_read(self, tmp_path):
        data_dir = tmp_path / "data"
        pointer = WorkflowPointerData(
            run_id="run_20260316_150000_abcd",
            workflow_id="stock_opportunity",
            completed_at=NOW.isoformat(),
            status="valid",
            output_filename="output.json",
            contract_version="1.0",
        )
        path = write_workflow_pointer(data_dir, "stock_opportunity", pointer)
        assert path.is_file()
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["run_id"] == "run_20260316_150000_abcd"
        assert content["status"] == "valid"


# ═══════════════════════════════════════════════════════════════════════
# Workflow artifact expectations
# ═══════════════════════════════════════════════════════════════════════


class TestWorkflowArtifactExpectations:
    def test_three_workflows_defined(self):
        assert len(WORKFLOW_ARTIFACT_EXPECTATIONS) == 3

    def test_market_intelligence_no_run_folders(self):
        mi = WORKFLOW_ARTIFACT_EXPECTATIONS["market_intelligence"]
        assert mi.uses_run_folders is False
        assert mi.uses_manifest is False
        assert mi.uses_pointer is True

    def test_stock_uses_run_folders_and_manifest(self):
        s = WORKFLOW_ARTIFACT_EXPECTATIONS["stock_opportunity"]
        assert s.uses_run_folders is True
        assert s.uses_manifest is True
        assert s.uses_pointer is True
        assert s.final_output_filename == OUTPUT_FILENAME

    def test_options_uses_run_folders_and_manifest(self):
        o = WORKFLOW_ARTIFACT_EXPECTATIONS["options_opportunity"]
        assert o.uses_run_folders is True
        assert o.uses_manifest is True
        assert o.stage_artifact_keys == (
            "load_market_state", "scan", "validate_math",
            "enrich_evaluate", "select_package",
        )

    def test_stock_stage_keys_match_definitions(self):
        s = WORKFLOW_ARTIFACT_EXPECTATIONS["stock_opportunity"]
        assert s.stage_artifact_keys == (
            "load_market_state", "scan", "normalize",
            "enrich_evaluate", "select_package",
        )

    @pytest.mark.parametrize("wid", [
        "market_intelligence", "stock_opportunity", "options_opportunity",
    ])
    def test_frozen(self, wid):
        exp = WORKFLOW_ARTIFACT_EXPECTATIONS[wid]
        with pytest.raises(AttributeError):
            exp.workflow_id = "x"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# Retention constant
# ═══════════════════════════════════════════════════════════════════════


class TestRetention:
    def test_default_retention_days(self):
        assert DEFAULT_RETENTION_DAYS == 7
