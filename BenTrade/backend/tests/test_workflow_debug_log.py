"""Targeted tests for workflow debug log infrastructure.

Tests cover:
- Debug log file creation
- Overwrite behavior per run
- Expected key sections being written
- Separate stock vs options file paths
- Safe serialization of complex objects
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.workflows.workflow_debug_log import WorkflowDebugLogger, _safe_serialize


# ═══════════════════════════════════════════════════════════════════════
# Test: File creation and overwrite
# ═══════════════════════════════════════════════════════════════════════


class TestDebugLogFileCreation:
    """Debug log file is created and overwritten correctly."""

    def test_creates_file_in_new_directory(self, tmp_path: Path) -> None:
        log_path = tmp_path / "subdir" / "test_debug.log"
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_001", workflow_id="test_workflow")
        dbg.close(status="completed")
        assert log_path.exists()

    def test_overwrites_on_second_run(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        # First run
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_FIRST", workflow_id="test_workflow")
        dbg.note("First run marker")
        dbg.close(status="completed")
        content1 = log_path.read_text(encoding="utf-8")
        assert "run_FIRST" in content1
        assert "First run marker" in content1

        # Second run — should overwrite
        dbg2 = WorkflowDebugLogger(log_path)
        dbg2.open(run_id="run_SECOND", workflow_id="test_workflow")
        dbg2.note("Second run marker")
        dbg2.close(status="completed")
        content2 = log_path.read_text(encoding="utf-8")
        assert "run_SECOND" in content2
        assert "Second run marker" in content2
        # First run content should be gone
        assert "run_FIRST" not in content2
        assert "First run marker" not in content2

    def test_file_not_appended(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        for i in range(3):
            dbg = WorkflowDebugLogger(log_path)
            dbg.open(run_id=f"run_{i}", workflow_id="test_workflow")
            dbg.close(status="completed")
        content = log_path.read_text(encoding="utf-8")
        # Only the last run should be present
        assert "run_2" in content
        assert "run_0" not in content
        assert "run_1" not in content


# ═══════════════════════════════════════════════════════════════════════
# Test: Expected sections
# ═══════════════════════════════════════════════════════════════════════


class TestDebugLogSections:
    """Debug log contains expected section markers."""

    def test_header_and_footer_present(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_test", workflow_id="stock_opportunity")
        dbg.close(status="completed", warnings=["test warning 1"])
        content = log_path.read_text(encoding="utf-8")
        assert "STOCK OPPORTUNITY WORKFLOW DEBUG LOG" in content
        assert "STOCK OPPORTUNITY WORKFLOW END" in content
        assert "run_test" in content
        assert "completed" in content
        assert "test warning 1" in content

    def test_stage_start_end_markers(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_test", workflow_id="stock_opportunity")
        dbg.stage_start("load_market_state", {"policy": "default"})
        dbg.stage_end("load_market_state", "completed", {"ref": "abc123"})
        dbg.close(status="completed")
        content = log_path.read_text(encoding="utf-8")
        assert "STAGE: load_market_state" in content
        assert "load_market_state → completed" in content
        assert "abc123" in content

    def test_section_divider(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_test", workflow_id="test_wf")
        dbg.section("Final Result")
        dbg.close(status="completed")
        content = log_path.read_text(encoding="utf-8")
        assert "Final Result" in content

    def test_detail_block(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_test", workflow_id="test_wf")
        dbg.detail("Config", {"top_n": 20, "data_dir": "/test"})
        dbg.close(status="completed")
        content = log_path.read_text(encoding="utf-8")
        assert "[Config]" in content
        assert '"top_n": 20' in content

    def test_candidates_block_with_limit(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_test", workflow_id="test_wf")
        cands = [{"symbol": f"SYM{i}", "score": i} for i in range(10)]
        dbg.candidates("Test candidates", cands, keys=["symbol", "score"], limit=3)
        dbg.close(status="completed")
        content = log_path.read_text(encoding="utf-8")
        assert "[Test candidates]" in content
        assert "SYM0" in content
        assert "SYM2" in content
        assert "7 more candidates omitted" in content

    def test_note_with_timestamp(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_test", workflow_id="test_wf")
        dbg.note("Model result: AAPL → rec=BUY score=85")
        dbg.close(status="completed")
        content = log_path.read_text(encoding="utf-8")
        assert "Model result: AAPL" in content
        assert "rec=BUY" in content

    def test_warnings_in_footer(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test_debug.log"
        dbg = WorkflowDebugLogger(log_path)
        dbg.open(run_id="run_test", workflow_id="test_wf")
        dbg.close(status="partial", warnings=[
            "[pipeline] Run interrupted",
            "[market_state] Degraded",
        ])
        content = log_path.read_text(encoding="utf-8")
        assert "partial" in content
        assert "[pipeline] Run interrupted" in content
        assert "[market_state] Degraded" in content


# ═══════════════════════════════════════════════════════════════════════
# Test: Separate stock vs options file paths
# ═══════════════════════════════════════════════════════════════════════


class TestSeparateFilePaths:
    """Stock and options use separate log file paths."""

    def test_stock_and_options_separate_files(self, tmp_path: Path) -> None:
        stock_path = tmp_path / "stock_pipeline_debug.log"
        options_path = tmp_path / "options_pipeline_debug.log"

        dbg_stock = WorkflowDebugLogger(stock_path)
        dbg_stock.open(run_id="stock_run_1", workflow_id="stock_opportunity")
        dbg_stock.note("Stock-specific data")
        dbg_stock.close(status="completed")

        dbg_options = WorkflowDebugLogger(options_path)
        dbg_options.open(run_id="options_run_1", workflow_id="options_opportunity")
        dbg_options.note("Options-specific data")
        dbg_options.close(status="completed")

        stock_content = stock_path.read_text(encoding="utf-8")
        options_content = options_path.read_text(encoding="utf-8")

        assert "stock_run_1" in stock_content
        assert "Stock-specific data" in stock_content
        assert "options_run_1" not in stock_content

        assert "options_run_1" in options_content
        assert "Options-specific data" in options_content
        assert "stock_run_1" not in options_content

    def test_runner_path_constants_are_different(self) -> None:
        from app.workflows.stock_opportunity_runner import _STOCK_DEBUG_LOG
        from app.workflows.options_opportunity_runner import _OPTIONS_DEBUG_LOG
        assert _STOCK_DEBUG_LOG != _OPTIONS_DEBUG_LOG
        assert "stock_pipeline_debug.log" in str(_STOCK_DEBUG_LOG)
        assert "options_pipeline_debug.log" in str(_OPTIONS_DEBUG_LOG)


# ═══════════════════════════════════════════════════════════════════════
# Test: Safe serialization
# ═══════════════════════════════════════════════════════════════════════


class TestSafeSerialization:
    """_safe_serialize handles various object types without crashing."""

    def test_none_and_primitives(self) -> None:
        assert _safe_serialize(None) is None
        assert _safe_serialize(42) == 42
        assert _safe_serialize(3.14) == 3.14
        assert _safe_serialize(True) is True
        assert _safe_serialize("hello") == "hello"

    def test_dict_and_list(self) -> None:
        result = _safe_serialize({"a": 1, "b": [2, 3]})
        assert result == {"a": 1, "b": [2, 3]}

    def test_datetime(self) -> None:
        dt = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)
        assert _safe_serialize(dt) == "2026-03-20T12:00:00+00:00"

    def test_path(self) -> None:
        p = Path("/some/path/file.json")
        result = _safe_serialize(p)
        assert "file.json" in result

    def test_set(self) -> None:
        result = _safe_serialize({3, 1, 2})
        assert result == [1, 2, 3]

    def test_dataclass(self) -> None:
        @dataclass
        class Sample:
            x: int = 1
            y: str = "hello"
        result = _safe_serialize(Sample())
        assert result == {"x": 1, "y": "hello"}

    def test_long_string_truncated(self) -> None:
        long_str = "x" * 5000
        result = _safe_serialize(long_str)
        assert len(result) < 5000
        assert "chars total" in result

    def test_deeply_nested(self) -> None:
        obj: dict = {"level": 0}
        current = obj
        for i in range(20):
            current["child"] = {"level": i + 1}
            current = current["child"]
        result = _safe_serialize(obj)
        assert isinstance(result, dict)

    def test_unserializable_object(self) -> None:
        class Mystery:
            pass
        result = _safe_serialize(Mystery())
        assert isinstance(result, str)
