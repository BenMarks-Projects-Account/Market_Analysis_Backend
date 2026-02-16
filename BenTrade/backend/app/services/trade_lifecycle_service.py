from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.utils.trade_key import trade_key


_EVENT_TO_STATE: dict[str, str] = {
    "CREATE": "CANDIDATE",
    "WATCHLIST": "WATCHLIST",
    "OPEN": "OPEN",
    "UPDATE": "MANAGED",
    "CLOSE": "CLOSED",
    "REJECT": "KNOWLEDGE_REJECTED",
    "NOTE": "NOTE",
    "EXPIRE": "EXPIRED",
    "CANCEL": "CANCELLED",
}

_ALLOWED_EVENTS = set(_EVENT_TO_STATE.keys())


class TradeLifecycleService:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.results_dir / "trade_ledger.jsonl"
        self._lock = RLock()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_event(value: Any) -> str:
        return str(value or "NOTE").strip().upper()

    @staticmethod
    def _normalize_source(value: Any) -> str:
        source = str(value or "unknown").strip().lower()
        return source if source else "unknown"

    @staticmethod
    def _extract_trade_key(value: Any, payload: dict[str, Any]) -> str:
        supplied = str(value or payload.get("trade_key") or payload.get("trade_id") or "").strip()
        if supplied:
            return supplied

        underlying = payload.get("underlying") or payload.get("underlying_symbol") or payload.get("symbol")
        expiration = payload.get("expiration")
        spread_type = payload.get("spread_type") or payload.get("strategy")
        short_strike = payload.get("short_strike")
        long_strike = payload.get("long_strike")
        dte = payload.get("dte")

        return trade_key(
            underlying=underlying,
            expiration=expiration,
            spread_type=spread_type,
            short_strike=short_strike,
            long_strike=long_strike,
            dte=dte,
        )

    def append_event(
        self,
        *,
        event: str,
        trade_key_value: str | None,
        source: str | None,
        payload: dict[str, Any] | None,
        user: str | None = "ben",
        reason: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        normalized_event = self._normalize_event(event)
        if normalized_event not in _ALLOWED_EVENTS:
            raise ValueError(f"Unsupported lifecycle event: {normalized_event}")

        event_payload = payload if isinstance(payload, dict) else {}
        resolved_key = self._extract_trade_key(trade_key_value, event_payload)
        if not resolved_key or resolved_key == "NA|NA|NA|NA|NA|NA":
            raise ValueError("trade_key is required")

        record = {
            "ts": self._utc_now_iso(),
            "event": normalized_event,
            "trade_key": resolved_key,
            "source": self._normalize_source(source),
            "payload": event_payload,
            "meta": {
                "user": str(user or "ben"),
                "reason": str(reason or ""),
                "note": str(note or ""),
            },
        }

        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

        return record

    def list_events(self) -> list[dict[str, Any]]:
        path = self.ledger_path
        if not path.exists():
            return []

        items: list[dict[str, Any]] = []
        with self._lock:
            with open(path, "r", encoding="utf-8") as fh:
                for row in fh:
                    row = row.strip()
                    if not row:
                        continue
                    try:
                        obj = json.loads(row)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        items.append(obj)
        return items

    def reconstruct_trades(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in self.list_events():
            key = str(event.get("trade_key") or "").strip()
            if not key:
                continue
            grouped.setdefault(key, []).append(event)

        out: list[dict[str, Any]] = []
        for key, events in grouped.items():
            events.sort(key=lambda item: str(item.get("ts") or ""))
            latest = events[-1]
            latest_payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
            latest_event = self._normalize_event(latest.get("event"))
            state = _EVENT_TO_STATE.get(latest_event, "CANDIDATE")
            if latest_event == "NOTE":
                state = None
                for prior in reversed(events[:-1]):
                    prior_event = self._normalize_event(prior.get("event"))
                    if prior_event == "NOTE":
                        continue
                    state = _EVENT_TO_STATE.get(prior_event, "CANDIDATE")
                    break
                state = state or "CANDIDATE"

            symbol = str(
                latest_payload.get("symbol")
                or latest_payload.get("underlying")
                or latest_payload.get("underlying_symbol")
                or ""
            ).upper()
            strategy = str(latest_payload.get("strategy") or latest_payload.get("spread_type") or "")
            realized = self._safe_float(latest_payload.get("realized_pnl"))

            out.append(
                {
                    "trade_key": key,
                    "state": state,
                    "symbol": symbol,
                    "strategy": strategy,
                    "updated_at": latest.get("ts"),
                    "source": latest.get("source"),
                    "latest_event": latest_event,
                    "latest_snapshot": latest_payload,
                    "realized_pnl": realized,
                    "events_count": len(events),
                }
            )

        out.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return out

    def get_trades(self, state: str | None = None) -> list[dict[str, Any]]:
        rows = self.reconstruct_trades()
        if not state:
            return rows
        target = str(state or "").strip().upper()
        return [row for row in rows if str(row.get("state") or "").upper() == target]

    def get_trade_history(self, trade_key_value: str) -> dict[str, Any]:
        key = str(trade_key_value or "").strip()
        events = [event for event in self.list_events() if str(event.get("trade_key") or "") == key]
        events.sort(key=lambda item: str(item.get("ts") or ""))

        latest = events[-1] if events else None
        latest_payload = latest.get("payload") if isinstance((latest or {}).get("payload"), dict) else {}
        latest_event = self._normalize_event((latest or {}).get("event")) if latest else ""

        state = _EVENT_TO_STATE.get(latest_event, "CANDIDATE") if latest_event else "UNKNOWN"
        if latest_event == "NOTE" and events:
            state = "CANDIDATE"
            for prior in reversed(events[:-1]):
                prior_event = self._normalize_event(prior.get("event"))
                if prior_event == "NOTE":
                    continue
                state = _EVENT_TO_STATE.get(prior_event, "CANDIDATE")
                break

        return {
            "trade_key": key,
            "state": state,
            "latest_snapshot": latest_payload,
            "history": events,
        }
