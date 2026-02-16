from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.utils.trade_key import trade_key


class DecisionService:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def build_trade_key(trade: dict[str, Any]) -> str:
        return trade_key(
            underlying=trade.get("underlying") or trade.get("underlying_symbol"),
            expiration=trade.get("expiration"),
            spread_type=trade.get("spread_type") or trade.get("strategy"),
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
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def append_reject(self, report_file: str, trade_key: str, reason: str | None = None) -> dict[str, Any]:
        decision = {
            "type": "reject",
            "trade_key": str(trade_key),
            "reason": reason or "manual_reject",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._decision_path(report_file)

        with self._lock:
            existing = self.list_decisions(report_file)
            existing.append(decision)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)

        return decision
