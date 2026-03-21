"""Per-workflow debug log writer.

Provides a dedicated debug logger that writes to a workflow-specific
log file under ``data/workflows/``.  Each workflow run overwrites the
file so it always reflects the latest run only.

Usage::

    dbg = WorkflowDebugLogger("data/workflows/stock_pipeline_debug.log")
    dbg.open(run_id="run_20260320_143000", workflow_id="stock_opportunity")
    dbg.stage_start("load_market_state", {"policy": ...})
    dbg.stage_end("load_market_state", "completed", {"ref": "abc"})
    dbg.close(status="completed", warnings=[...])

The log is human-readable with timestamps, section dividers, and
pretty-printed payloads for easy visual scanning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum characters for a single payload dump to keep the log
# readable.  Individual fields are truncated, not the whole block.
_MAX_COLLECTION_ITEMS = 200
_MAX_STR_LEN = 2000


def _safe_serialize(obj: Any, depth: int = 0) -> Any:
    """Recursively convert an object to a JSON-safe representation.

    Handles dataclasses, datetimes, Paths, sets, and arbitrary objects
    so the log never crashes on un-serializable data.
    """
    if depth > 8:
        return "<max depth>"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        if len(obj) > _MAX_STR_LEN:
            return obj[:_MAX_STR_LEN] + f"... ({len(obj)} chars total)"
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}>"
    if isinstance(obj, set):
        return [_safe_serialize(v, depth + 1) for v in sorted(obj, key=str)]
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return _safe_serialize(asdict(obj), depth + 1)
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        return {
            str(k): _safe_serialize(v, depth + 1)
            for k, v in list(obj.items())[:_MAX_COLLECTION_ITEMS]
        }
    if isinstance(obj, (list, tuple)):
        items = list(obj)[:_MAX_COLLECTION_ITEMS]
        result = [_safe_serialize(v, depth + 1) for v in items]
        if len(obj) > _MAX_COLLECTION_ITEMS:
            result.append(f"... ({len(obj)} items total, showing {_MAX_COLLECTION_ITEMS})")
        return result
    # Fallback: try to_dict(), then str()
    if hasattr(obj, "to_dict"):
        try:
            return _safe_serialize(obj.to_dict(), depth + 1)
        except Exception:
            pass
    return str(obj)


def _pretty(obj: Any, indent: int = 2) -> str:
    """Pretty-print an object safely."""
    try:
        safe = _safe_serialize(obj)
        return json.dumps(safe, indent=indent, default=str)
    except Exception:
        return str(obj)


class WorkflowDebugLogger:
    """Writes a human-readable debug log for one workflow run.

    The file is opened in **overwrite** mode at ``open()`` so only the
    latest run is present.  All writes are flushed immediately.
    """

    def __init__(self, log_path: str | Path) -> None:
        self._path = Path(log_path)
        self._fh: Any = None
        self._run_id: str = ""
        self._workflow_id: str = ""
        self._start_time: datetime | None = None

    # ── lifecycle ────────────────────────────────────────────────

    def open(self, run_id: str, workflow_id: str) -> None:
        """Open (overwrite) the log file and write the header."""
        self._run_id = run_id
        self._workflow_id = workflow_id
        self._start_time = datetime.now(timezone.utc)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "w", encoding="utf-8")  # noqa: SIM115
        except Exception as exc:
            logger.warning("Failed to open debug log %s: %s", self._path, exc)
            self._fh = None
            return
        wid = workflow_id.upper().replace("_", " ")
        self._write_line(f"{'=' * 72}")
        self._write_line(f"  {wid} WORKFLOW DEBUG LOG")
        self._write_line(f"{'=' * 72}")
        self._write_line(f"  Run ID      : {run_id}")
        self._write_line(f"  Workflow     : {workflow_id}")
        self._write_line(f"  Started      : {self._start_time.isoformat()}")
        self._write_line(f"{'=' * 72}")
        self._write_line("")

    def close(
        self,
        status: str = "completed",
        warnings: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write the footer and close the file."""
        end_time = datetime.now(timezone.utc)
        elapsed = (
            (end_time - self._start_time).total_seconds()
            if self._start_time
            else 0.0
        )
        self._write_line("")
        wid = self._workflow_id.upper().replace("_", " ")
        self._write_line(f"{'=' * 72}")
        self._write_line(f"  {wid} WORKFLOW END")
        self._write_line(f"{'=' * 72}")
        self._write_line(f"  Status       : {status}")
        self._write_line(f"  Finished     : {end_time.isoformat()}")
        self._write_line(f"  Elapsed      : {elapsed:.2f}s")
        if warnings:
            self._write_line(f"  Warnings ({len(warnings)}):")
            for w in warnings:
                self._write_line(f"    - {w}")
        if extra:
            self._write_line("  Extra:")
            self._write_line(_pretty(extra))
        self._write_line(f"{'=' * 72}")
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    # ── stage helpers ────────────────────────────────────────────

    def stage_start(self, stage_name: str, inputs: dict[str, Any] | None = None) -> None:
        """Log the start of a pipeline stage with optional inputs."""
        ts = datetime.now(timezone.utc).isoformat()
        self._write_line(f"{'─' * 72}")
        self._write_line(f"▶ STAGE: {stage_name}")
        self._write_line(f"  Timestamp : {ts}")
        if inputs:
            self._write_line("  Inputs:")
            self._write_line(_pretty(inputs))
        self._write_line("")

    def stage_end(
        self,
        stage_name: str,
        status: str,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        """Log the end of a pipeline stage with optional outputs."""
        ts = datetime.now(timezone.utc).isoformat()
        self._write_line(f"  ✓ {stage_name} → {status}  [{ts}]")
        if outputs:
            self._write_line("  Outputs:")
            self._write_line(_pretty(outputs))
        self._write_line("")

    # ── general helpers ──────────────────────────────────────────

    def section(self, title: str) -> None:
        """Write a named section divider."""
        self._write_line(f"{'─' * 72}")
        self._write_line(f"  {title}")
        self._write_line(f"{'─' * 72}")

    def detail(self, label: str, data: Any) -> None:
        """Write a labeled data block."""
        self._write_line(f"  [{label}]")
        self._write_line(_pretty(data))
        self._write_line("")

    def note(self, message: str) -> None:
        """Write a single-line note."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        self._write_line(f"  [{ts}] {message}")

    def candidates(
        self,
        label: str,
        cands: list[dict[str, Any]],
        keys: list[str] | None = None,
        limit: int = 50,
    ) -> None:
        """Log a candidate list with optional key filtering and limit."""
        self._write_line(f"  [{label}] ({len(cands)} total, showing up to {limit})")
        for i, c in enumerate(cands[:limit]):
            if keys:
                filtered = {k: c.get(k) for k in keys}
                self._write_line(f"    [{i + 1}] {json.dumps(_safe_serialize(filtered), default=str)}")
            else:
                self._write_line(f"    [{i + 1}] {json.dumps(_safe_serialize(c), default=str)}")
        if len(cands) > limit:
            self._write_line(f"    ... {len(cands) - limit} more candidates omitted")
        self._write_line("")

    # ── internal ─────────────────────────────────────────────────

    def _write_line(self, line: str) -> None:
        """Write a line and flush."""
        if self._fh is None:
            return
        try:
            self._fh.write(line + "\n")
            self._fh.flush()
        except Exception:
            pass
