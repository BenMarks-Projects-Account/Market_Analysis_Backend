from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal

Severity = Literal["warn", "error"]


class ValidationEventsService:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.results_dir / "validation_events.jsonl"
        self.path.touch(exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_severity(value: str) -> Severity:
        severity = str(value or "warn").strip().lower()
        if severity == "error":
            return "error"
        return "warn"

    def append_event(
        self,
        *,
        severity: str,
        code: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "ts": self._utc_now_iso(),
            "severity": self._normalize_severity(severity),
            "code": str(code or "UNKNOWN").strip().upper() or "UNKNOWN",
            "message": str(message or "").strip() or "validation event",
            "context": context if isinstance(context, dict) else {},
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read_recent(self, *, limit: int = 200) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        if not self.path.exists():
            return []

        rows: list[dict[str, Any]] = []
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as handle:
                for raw in handle:
                    text = raw.strip()
                    if not text:
                        continue
                    try:
                        obj = json.loads(text)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        rows.append(obj)
        return rows[-limit:]


def build_rollups(events: list[dict[str, Any]]) -> dict[str, Any]:
    code_counter: Counter[str] = Counter()
    severity_counter: Counter[str] = Counter()
    most_recent_by_code: dict[str, dict[str, Any]] = {}

    for event in events:
        if not isinstance(event, dict):
            continue
        code = str(event.get("code") or "UNKNOWN").strip().upper() or "UNKNOWN"
        severity = str(event.get("severity") or "warn").strip().lower() or "warn"

        code_counter[code] += 1
        severity_counter[severity] += 1

        current = most_recent_by_code.get(code)
        if current is None or str(event.get("ts") or "") >= str(current.get("ts") or ""):
            most_recent_by_code[code] = event

    top_codes = [
        {"code": code, "count": count}
        for code, count in code_counter.most_common(10)
    ]

    return {
        "counts_by_code": dict(code_counter),
        "counts_by_severity": dict(severity_counter),
        "most_recent_by_code": most_recent_by_code,
        "top_codes": top_codes,
    }


_DEFAULT_SERVICE: ValidationEventsService | None = None


def emit_validation_event(
    *,
    severity: str,
    code: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> None:
    global _DEFAULT_SERVICE
    try:
        if _DEFAULT_SERVICE is None:
            backend_dir = Path(__file__).resolve().parents[2]
            _DEFAULT_SERVICE = ValidationEventsService(backend_dir / "results")
        _DEFAULT_SERVICE.append_event(
            severity=severity,
            code=code,
            message=message,
            context=context,
        )
    except Exception:
        return
