"""Report conformance validation.

Provides a single check that determines whether a persisted report file
is loadable.  Truly corrupt files (invalid JSON, missing root keys) are
deleted.  Reports with zero trades are kept and annotated with
``report_status: "empty"``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CANONICAL_TRADE_FIELDS = frozenset({"trade_key", "strategy_id", "computed", "details", "pills"})

# Root keys that *must* exist for the file to be considered loadable.
_REQUIRED_ROOT_KEYS = frozenset({"strategyId"})


def is_conforming_report(data: Any) -> bool:
    """Return True if *data* looks like a valid canonical report.

    A conforming report is a dict with a non-empty ``trades`` list where
    every trade contains at least the canonical fields:
    ``trade_key``, ``strategy_id``, ``computed``, ``details``, ``pills``.
    """
    if not isinstance(data, dict):
        return False
    trades = data.get("trades")
    if not isinstance(trades, list) or len(trades) == 0:
        return False
    return all(
        isinstance(t, dict) and _CANONICAL_TRADE_FIELDS.issubset(t.keys())
        for t in trades
    )


def is_loadable_report(data: Any) -> bool:
    """Return True if *data* is a dict that can be served to the frontend.

    A loadable report must be a dict with at least ``strategyId``.  Empty
    trades are allowed — the file is annotated rather than deleted.
    """
    if not isinstance(data, dict):
        return False
    if not _REQUIRED_ROOT_KEYS.issubset(data.keys()):
        return False
    return True


def _annotate_empty_report(data: dict[str, Any]) -> None:
    """Mark a report whose trades list is empty with status metadata."""
    trades = data.get("trades")
    if isinstance(trades, list) and len(trades) > 0:
        data.setdefault("report_status", "ok")
        return
    # Ensure trades is at least an empty list
    if not isinstance(trades, list):
        data["trades"] = []
    data["report_status"] = "empty"
    warnings = data.get("report_warnings")
    if not isinstance(warnings, list):
        warnings = []
        data["report_warnings"] = warnings
    default_warning = "No trades generated (all candidates filtered out or invalid quotes)."
    if default_warning not in warnings:
        warnings.append(default_warning)


def validate_report_file(
    path: Path,
    *,
    validation_events: Any | None = None,
    auto_delete: bool = True,
) -> dict[str, Any] | None:
    """Load, validate, and return a report.

    Truly corrupt files (unparseable JSON, missing required root keys) are
    deleted when *auto_delete* is True.  Reports with zero trades are
    **kept** and annotated with ``report_status="empty"``.

    Parameters
    ----------
    path:
        Path to the JSON report file.
    validation_events:
        Optional ``ValidationEventsService`` instance.  When provided a
        ``NON_CONFORMING_FILE_ENCOUNTERED`` event is emitted for corrupt
        files.
    auto_delete:
        When *True* (default) *corrupt* files are unlinked from disk.
        Empty-trades reports are never deleted.

    Returns
    -------
    dict | None
        The parsed report dict if loadable, otherwise ``None``.
    """
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = None

    # Fully conforming (non-empty trades with canonical fields) — fast path.
    if is_conforming_report(data):
        _annotate_empty_report(data)  # sets report_status="ok"
        return data  # type: ignore[return-value]

    # Loadable but with zero trades — keep and annotate.
    if is_loadable_report(data):
        _annotate_empty_report(data)
        reason = _lint_reason(data)
        if reason:
            logger.info("Report file kept with annotation: %s (%s)", path.name, reason)
            if validation_events is not None:
                try:
                    validation_events.append_event(
                        severity="info",
                        code="REPORT_EMPTY_KEPT",
                        message=f"Report kept with empty trades: {path.name}",
                        context={"filename": path.name, "reason": reason},
                    )
                except Exception:
                    pass
        return data  # type: ignore[return-value]

    # --- truly corrupt / un-loadable path ---
    reason = _lint_reason(data)
    logger.warning("Non-conforming report file deleted: %s (%s)", path.name, reason)

    if validation_events is not None:
        try:
            validation_events.append_event(
                severity="error",
                code="NON_CONFORMING_FILE_ENCOUNTERED",
                message=f"Deleted non-conforming report: {path.name}",
                context={"filename": path.name, "reason": reason},
            )
        except Exception:
            pass  # best-effort

    if auto_delete:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to delete non-conforming file: %s", path.name)

    return None


def _lint_reason(data: Any) -> str:
    if data is None:
        return "invalid JSON"
    if not isinstance(data, dict):
        return "top-level is not a dict"
    missing_root = _REQUIRED_ROOT_KEYS - set(data.keys())
    if missing_root:
        return f"missing required root keys: {sorted(missing_root)}"
    trades = data.get("trades")
    if not isinstance(trades, list):
        return "no trades array"
    if len(trades) == 0:
        return "trades array is empty"
    missing: set[str] = set()
    for t in trades:
        if not isinstance(t, dict):
            return "trade entry is not a dict"
        missing.update(_CANONICAL_TRADE_FIELDS - set(t.keys()))
    if missing:
        return f"trades missing canonical fields: {sorted(missing)}"
    return ""
