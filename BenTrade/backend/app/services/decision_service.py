from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.services.validation_events import ValidationEventsService
from app.utils.trade_key import canonicalize_strategy_id, canonicalize_trade_key, trade_key


class DecisionService:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.validation_events = ValidationEventsService(results_dir=self.results_dir)
        self._lock = RLock()

    def _emit_validation_event(
        self,
        *,
        severity: str,
        code: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.validation_events.append_event(
                severity=severity,
                code=code,
                message=message,
                context=context,
            )
        except Exception:
            return

    @staticmethod
    def build_trade_key(trade: dict[str, Any]) -> str:
        canonical_strategy, _, _ = canonicalize_strategy_id(trade.get("spread_type") or trade.get("strategy"))
        return trade_key(
            underlying=trade.get("underlying") or trade.get("underlying_symbol"),
            expiration=trade.get("expiration"),
            spread_type=canonical_strategy,
            short_strike=trade.get("short_strike"),
            long_strike=trade.get("long_strike"),
            dte=trade.get("dte"),
        )

    def _decision_path(self, report_file: str) -> Path:
        report_name = Path(report_file).name
        return self.results_dir / f"decisions_{report_name}.json"

    def list_decisions(self, report_file: str) -> list[dict[str, Any]]:
        path = self._decision_path(report_file)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = []
        except Exception:
            return []

        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in data if isinstance(data, list) else []:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            original_key = str(item.get("trade_key") or "").strip()
            canonical_key = canonicalize_trade_key(original_key)
            if original_key and canonical_key and original_key != canonical_key:
                self._emit_validation_event(
                    severity="warn",
                    code="TRADE_KEY_NON_CANONICAL",
                    message="Decision entry trade_key was not canonical and was rewritten",
                    context={
                        "report_file": Path(report_file).name,
                        "trade_key": canonical_key,
                        "provided_trade_key": original_key,
                    },
                )
            item["trade_key"] = canonical_key or original_key

            dedupe_key = (
                str(item.get("type") or "").strip().lower(),
                str(item.get("trade_key") or "").strip(),
                str(item.get("reason") or "").strip().lower(),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(item)
        return normalized

    def append_reject(self, report_file: str, trade_key: str, reason: str | None = None) -> dict[str, Any]:
        provided_key = str(trade_key or "").strip()
        parts = provided_key.split("|") if provided_key else []
        if len(parts) == 6:
            canonical_strategy, alias_mapped, provided_strategy = canonicalize_strategy_id(parts[2])
            if alias_mapped and canonical_strategy:
                self._emit_validation_event(
                    severity="warn",
                    code="TRADE_STRATEGY_ALIAS_MAPPED",
                    message="Decision reject strategy alias mapped to canonical strategy_id",
                    context={
                        "report_file": Path(report_file).name,
                        "strategy_id": canonical_strategy,
                        "provided_strategy": provided_strategy,
                    },
                )

        canonical_key = canonicalize_trade_key(trade_key)
        if str(trade_key or "").strip() and canonical_key and canonical_key != str(trade_key).strip():
            self._emit_validation_event(
                severity="warn",
                code="TRADE_KEY_NON_CANONICAL",
                message="Decision append received non-canonical trade_key",
                context={
                    "report_file": Path(report_file).name,
                    "trade_key": canonical_key,
                    "provided_trade_key": str(trade_key or "").strip(),
                },
            )

        decision = {
            "type": "reject",
            "trade_key": canonical_key or str(trade_key),
            "reason": reason or "manual_reject",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._decision_path(report_file)

        with self._lock:
            existing = self.list_decisions(report_file)
            signature = (
                str(decision.get("type") or "").strip().lower(),
                str(decision.get("trade_key") or "").strip(),
                str(decision.get("reason") or "").strip().lower(),
            )
            if any(
                (
                    str(item.get("type") or "").strip().lower(),
                    str(item.get("trade_key") or "").strip(),
                    str(item.get("reason") or "").strip().lower(),
                )
                == signature
                for item in existing
                if isinstance(item, dict)
            ):
                return decision
            existing.append(decision)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)

        return decision
