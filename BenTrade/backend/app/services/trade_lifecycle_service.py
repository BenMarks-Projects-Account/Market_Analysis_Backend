from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.utils.strategy_id_resolver import resolve_strategy_id_or_none
from app.utils.trade_key import canonicalize_spread_type, canonicalize_strategy_id, canonicalize_trade_key, trade_key
from app.services.validation_events import ValidationEventsService


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
        self.validation_events = ValidationEventsService(results_dir=self.results_dir)
        self._lock = RLock()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return parsed

    @staticmethod
    def _sanitize_finite(value: Any, *, path: str = "payload", warnings: list[str] | None = None) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            parsed = float(value)
            if math.isfinite(parsed):
                return value
            if warnings is not None:
                warnings.append(f"NON_FINITE:{path}")
            return None
        if isinstance(value, list):
            out: list[Any] = []
            for index, item in enumerate(value):
                out.append(
                    TradeLifecycleService._sanitize_finite(
                        item,
                        path=f"{path}[{index}]",
                        warnings=warnings,
                    )
                )
            return out
        if isinstance(value, dict):
            out_dict: dict[str, Any] = {}
            for key, val in value.items():
                key_name = str(key)
                out_dict[key_name] = TradeLifecycleService._sanitize_finite(
                    val,
                    path=f"{path}.{key_name}",
                    warnings=warnings,
                )
            return out_dict
        return value

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

        underlying = payload.get("underlying") or payload.get("underlying_symbol") or payload.get("symbol")
        expiration = payload.get("expiration")
        spread_type = payload.get("spread_type") or payload.get("strategy")
        short_strike = payload.get("short_strike")
        long_strike = payload.get("long_strike")
        dte = payload.get("dte")

        if any(
            item not in (None, "")
            for item in (underlying, expiration, spread_type, short_strike, long_strike, dte)
        ):
            return trade_key(
                underlying=underlying,
                expiration=expiration,
                spread_type=spread_type,
                short_strike=short_strike,
                long_strike=long_strike,
                dte=dte,
            )
        return canonicalize_trade_key(supplied)

    @staticmethod
    def _canonicalize_payload(payload: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
        out = dict(payload)

        raw_spread = out.get("spread_type") or out.get("strategy")
        # Single resolver: emits STRATEGY_ALIAS_USED for aliases.
        canonical_spread = resolve_strategy_id_or_none(raw_spread)
        _, alias_mapped, _ = canonicalize_strategy_id(raw_spread)
        if canonical_spread:
            if str(out.get("spread_type") or "").strip().lower() != canonical_spread:
                warnings.append("SPREAD_TYPE_CANONICALIZED")
            if alias_mapped:
                warnings.append("TRADE_STRATEGY_ALIAS_MAPPED")
            out["spread_type"] = canonical_spread
            out["strategy"] = canonical_spread
            out["strategy_id"] = canonical_spread

        if out.get("model_evaluation") is None:
            out["model_status"] = "unavailable"
            warnings.append("MODEL_UNAVAILABLE")

        return out

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

        write_warnings: list[str] = []
        event_payload = payload if isinstance(payload, dict) else {}
        event_payload = self._canonicalize_payload(event_payload, write_warnings)
        event_payload = self._sanitize_finite(event_payload, warnings=write_warnings)

        supplied_key = str(trade_key_value or event_payload.get("trade_key") or "").strip()
        resolved_key = self._extract_trade_key(trade_key_value, event_payload)
        supplied_parts = supplied_key.split("|") if supplied_key else []
        if len(supplied_parts) == 6:
            _, alias_mapped, _ = canonicalize_strategy_id(supplied_parts[2])
            if alias_mapped:
                write_warnings.append("TRADE_STRATEGY_ALIAS_MAPPED")
        if supplied_key and supplied_key != resolved_key:
            write_warnings.append("TRADE_KEY_NON_CANONICAL")

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
        if write_warnings:
            record["warnings"] = sorted(set(write_warnings))

        for warning in write_warnings:
            if warning == "TRADE_KEY_NON_CANONICAL":
                self._emit_validation_event(
                    severity="warn",
                    code="TRADE_KEY_NON_CANONICAL",
                    message="Trade key was rewritten to canonical format",
                    context={
                        "source": self._normalize_source(source),
                        "trade_key": resolved_key,
                        "provided_trade_key": supplied_key,
                    },
                )
            elif warning == "MODEL_UNAVAILABLE":
                self._emit_validation_event(
                    severity="warn",
                    code="MODEL_UNAVAILABLE",
                    message="Model evaluation unavailable; treated as operational",
                    context={
                        "source": self._normalize_source(source),
                        "trade_key": resolved_key,
                    },
                )
            elif warning == "TRADE_STRATEGY_ALIAS_MAPPED":
                self._emit_validation_event(
                    severity="warn",
                    code="TRADE_STRATEGY_ALIAS_MAPPED",
                    message="Inbound strategy alias mapped to canonical strategy_id",
                    context={
                        "source": self._normalize_source(source),
                        "trade_key": resolved_key,
                        "strategy_id": str((event_payload or {}).get("strategy") or (event_payload or {}).get("spread_type") or ""),
                    },
                )
            elif warning.startswith("NON_FINITE:"):
                self._emit_validation_event(
                    severity="error",
                    code="NUMERIC_NONFINITE",
                    message="Non-finite numeric value was sanitized before ledger persistence",
                    context={
                        "source": self._normalize_source(source),
                        "trade_key": resolved_key,
                        "path": warning.split(":", 1)[1] if ":" in warning else "payload",
                    },
                )

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
        key = canonicalize_trade_key(trade_key_value)
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
